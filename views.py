import logging
from typing import TYPE_CHECKING

import discord
import challonge 

from ballsdex.core.models import Player
from .models import Tournament, TournamentPlayer, TournamentState

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.tournament.views")


class TournamentRegistrationView(discord.ui.View):
    """Simple view for tournament registration only"""
    
    def __init__(self, tournament: Tournament):
        super().__init__(timeout=None)
        self.tournament = tournament
        
    @discord.ui.button(label="Join Tournament", style=discord.ButtonStyle.primary)
    async def join_tournament(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_join(interaction)
        
    @discord.ui.button(label="Leave Tournament", style=discord.ButtonStyle.secondary)
    async def leave_tournament(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_leave(interaction)
        
    @discord.ui.button(label="Start Tournament", style=discord.ButtonStyle.success)
    async def start_tournament(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_start(interaction)
        
    @discord.ui.button(label="Cancel Tournament", style=discord.ButtonStyle.danger)
    async def cancel_tournament(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._handle_cancel(interaction)
    
    async def _handle_join(self, interaction: discord.Interaction):
        """Handle join tournament button"""
        # Check if user is already in tournament
        for participant in self.tournament.participants:
            if participant.user.id == interaction.user.id:
                await interaction.response.send_message(
                    "You are already registered for this tournament!", ephemeral=True
                )
                return
        
        # Check if tournament is full
        if len(self.tournament.participants) >= self.tournament.max_participants:
            await interaction.response.send_message(
                "Tournament is full!", ephemeral=True
            )
            return
            
        # Check if registration is still open
        if self.tournament.state != TournamentState.REGISTRATION:
            await interaction.response.send_message(
                "Registration is closed for this tournament!", ephemeral=True
            )
            return
        
        # Create player
        player, _ = await Player.get_or_create(discord_id=interaction.user.id)
        participant = TournamentPlayer(
            user=interaction.user,
            player_id=player.pk
        )
        
        self.tournament.participants.append(participant)
        
        # Add to Challonge
        try:
            challonge_participant = challonge.participants.create(
                self.tournament.challonge_tournament["id"],
                interaction.user.display_name
            )
            self.tournament.challonge_participants[participant.user.id] = challonge_participant
        except Exception as e:
            log.error(f"Failed to add participant to Challonge: {e}")
        
        await interaction.response.send_message(
            f"✅ You have joined the tournament! ({len(self.tournament.participants)}/{self.tournament.max_participants})",
            ephemeral=True
        )
        
    async def _handle_leave(self, interaction: discord.Interaction):
        """Handle leave tournament button"""
        participant_to_remove = None
        for participant in self.tournament.participants:
            if participant.user.id == interaction.user.id:
                participant_to_remove = participant
                break
        
        if not participant_to_remove:
            await interaction.response.send_message(
                "You are not registered for this tournament!", ephemeral=True
            )
            return
            
        if self.tournament.state != TournamentState.REGISTRATION:
            await interaction.response.send_message(
                "You cannot leave once the tournament has started!", ephemeral=True
            )
            return
        
        self.tournament.participants.remove(participant_to_remove)
        
        # Remove from Challonge
        try:
            challonge_participant = self.tournament.challonge_participants.get(interaction.user.id)
            if challonge_participant:
                challonge.participants.destroy(
                    self.tournament.challonge_tournament["id"],
                    challonge_participant["id"]
                )
                del self.tournament.challonge_participants[interaction.user.id]
        except Exception as e:
            log.error(f"Failed to remove participant from Challonge: {e}")
        
        await interaction.response.send_message(
            "❌ You have left the tournament!", ephemeral=True
        )
        
        embed = self._create_registration_embed()
        await interaction.edit_original_response(embed=embed)
    
    async def _handle_start(self, interaction: discord.Interaction):
        """Handle start tournament button - starts the tournament and removes view"""
        if interaction.user.id != self.tournament.organizer.id:
            await interaction.response.send_message(
                "You Dond have permmision to start this tournament!", ephemeral=True
            )
            return
            
        if len(self.tournament.participants) < 2:
            await interaction.response.send_message(
                "Need at least 2 participants to start the tournament!", ephemeral=True
            )
            return
            
        if self.tournament.state != TournamentState.REGISTRATION:
            await interaction.response.send_message(
                "Tournament has already been started!", ephemeral=True
            )
            return
        
        await interaction.response.defer()
        
        # Import here to avoid circular imports
        from .cog import auto_select_balls_for_tournament
        
        try:
            # Auto-select balls for all participants
            await auto_select_balls_for_tournament(self.tournament, interaction.client)
            
            # Start Challonge tournament
            challonge.tournaments.start(self.tournament.challonge_tournament["id"])
            self.tournament.state = TournamentState.ACTIVE
            
            # Create final embed with no buttons
            embed = discord.Embed(
                title=f"{self.tournament.name}",
                description="Tournament has started! Battles will be processed automatically.",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="📊 Tournament Info",
                value=f"**Participants:** {len(self.tournament.participants)}\n**Type:** {self.tournament.tournament_type.value.title()}",
                inline=False
            )
            
            if self.tournament.challonge_tournament:
                embed.add_field(
                    name="📈 Tournament Bracket",
                    value=f"[View on Challonge]({self.tournament.challonge_tournament['full_challonge_url']})",
                    inline=False
                )
            
            # Remove view - tournament is now active
            await interaction.edit_original_response(embed=embed, view=None)
            
        except Exception as e:
            log.error(f"Failed to start tournament: {e}")
            embed = discord.Embed(
                title=f"❌ {self.tournament.name}",
                description=f"Failed to start tournament: {str(e)[:200]}",
                color=discord.Color.red()
            )
            await interaction.edit_original_response(embed=embed)
    
    async def _handle_cancel(self, interaction: discord.Interaction):
        """Handle cancel tournament button"""
        if interaction.user.id != self.tournament.organizer.id:
            await interaction.response.send_message(
                "You Dond have permission to cancel this tournament!", ephemeral=True
            )
            return
        
        self.tournament.state = TournamentState.CANCELLED
        
        # Cancel Challonge tournament
        try:
            challonge.tournaments.destroy(self.tournament.challonge_tournament["id"])
        except Exception as e:
            log.error(f"Failed to cancel Challonge tournament: {e}")
        
        embed = discord.Embed(
            title=f"❌ Tournament Cancelled",
            description=f"**{self.tournament.name}** has been cancelled.",
            color=discord.Color.red()
        )
        
        await interaction.response.edit_message(embed=embed, view=None)
    
    def _create_registration_embed(self) -> discord.Embed:
        """Create embed for registration phase"""
        embed = discord.Embed(
            title=f"🏆 {self.tournament.name}",
            description=f"**Type:** {self.tournament.tournament_type.value.title()}\n**State:** Registration Open",
            color=discord.Color.gold()
        )
        
        # Configuration
        config_text = f"**Max Participants:** {self.tournament.max_participants}\n"
        config_text += f"**Balls per Player:** {self.tournament.balls_per_player}\n"
        
        if self.tournament.min_rarity is not None:
            config_text += f"**Min Rarity:** {self.tournament.min_rarity}\n"
        if self.tournament.max_rarity is not None:
            config_text += f"**Max Rarity:** {self.tournament.max_rarity}\n"
        
        config_text += f"**Special Balls:** {'Allowed' if self.tournament.special_allowed else 'Not Allowed'}\n"
        config_text += f"**Duplicates:** {'Allowed' if self.tournament.duplicates_allowed else 'Not Allowed'}"
        
        embed.add_field(name="⚙️ Configuration", value=config_text, inline=False)
        
        # Participants
        if self.tournament.participants:
            participant_list = "\n".join([f"• {p.user.display_name}" for p in self.tournament.participants])
            if len(participant_list) > 1024:
                participant_list = participant_list[:1021] + "..."
        else:
            participant_list = "No participants yet"
        
        embed.add_field(
            name=f"👥 Participants ({len(self.tournament.participants)}/{self.tournament.max_participants})",
            value=participant_list,
            inline=False
        )
        
        embed.set_footer(text=f"Organized by {self.tournament.organizer.display_name}")
        return embed
