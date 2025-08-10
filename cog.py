# ballsdx/packages/tournament/cog.py
import logging
import random
import re
import asyncio
import traceback 
import io
import os
from typing import TYPE_CHECKING, Optional
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands, tasks
import challonge

from ballsdex.core.models import Player, BallInstance

from .models import Tournament, TournamentType, TournamentState, TournamentPlayer
from .views import TournamentRegistrationView
from .battle_utils import BattleBall, simulate_tournament_battle

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.tournament.cog")

# Global tournament storage
active_tournaments = {}


@dataclass
class TournamentBall:
    """Tournament ball representation"""
    country: str
    emoji: str
    health: int
    attack: int
    rarity: float
    special: Optional[str] = None


async def auto_select_balls_for_tournament(tournament: Tournament, bot):
    """Auto-select balls for all tournament participants"""
    failed_participants = []
    
    for participant in tournament.participants:
        try:
            player = await Player.get(pk=participant.player_id)
            
            # Get all ball instances for the player
            query = BallInstance.filter(player=player).select_related('ball', 'special')
            ball_instances = await query
            
            # Filter based on tournament criteria
            eligible_instances = []
            for instance in ball_instances:
                ball = instance.ball
                
                # Check rarity constraints
                if tournament.min_rarity is not None and ball.rarity < tournament.min_rarity:
                    continue
                if tournament.max_rarity is not None and ball.rarity > tournament.max_rarity:
                    continue
                
                # Check special constraint
                if not tournament.special_allowed and instance.special:
                    continue
                
                # Check if ball is enabled and tradeable
                if not ball.enabled or not ball.tradeable:
                    continue
                    
                eligible_instances.append(instance)
            
            # Handle duplicates
            if not tournament.duplicates_allowed:
                # Group by ball ID and select best instance of each
                ball_groups = {}
                for instance in eligible_instances:
                    ball_id = instance.ball.id
                    if ball_id not in ball_groups:
                        ball_groups[ball_id] = []
                    ball_groups[ball_id].append(instance)
                
                # Select best instance from each group (highest combined stats)
                selected_instances = []
                for group in ball_groups.values():
                    best = max(group, key=lambda x: x.health + x.attack)
                    selected_instances.append(best)
            else:
                selected_instances = eligible_instances
            
            # Check if player has enough balls
            if len(selected_instances) < tournament.balls_per_player:
                failed_participants.append((participant, len(selected_instances)))
                continue
            
            # Shuffle and select required number
            random.shuffle(selected_instances)
            selected_instances = selected_instances[:tournament.balls_per_player]
            
            # Convert to TournamentBall objects
            participant.balls = []
            for instance in selected_instances:
                ball = instance.ball
                
                # Get actual emoji from bot
                emoji = "🏀"  # Default fallback
                try:
                    discord_emoji = bot.get_emoji(ball.emoji_id)
                    if discord_emoji:
                        emoji = str(discord_emoji)
                except Exception:
                    pass
                
                tournament_ball = TournamentBall(
                    country=ball.country,
                    emoji=emoji,
                    health=instance.health,
                    attack=instance.attack,
                    rarity=ball.rarity,
                    special=instance.special.name if instance.special else None
                )
                participant.balls.append(tournament_ball)
                
        except Exception as e:
            log.error(f"Failed to auto-select balls for {participant.user.display_name}: {e}")
            failed_participants.append((participant, 0))
    
    # Remove failed participants
    for participant, _ in failed_participants:
        if participant in tournament.participants:
            tournament.participants.remove(participant)
    
    return failed_participants

def _slugify(text: str) -> str:
    s = (text or "").lower()
    s = re.sub(r'[^a-z0-9_]+', '_', s)  # Replace any char NOT in a-z,0-9,_ with underscore
    s = re.sub(r'_+', '_', s)           # Collapse repeated underscores
    s = s.strip('_')                    # Strip leading/trailing underscores
    return s or f"tourney_{random.randint(1000, 9999)}"
    
class TournamentCog(commands.GroupCog, name="tournament"):
    """Simplified tournament system using existing battle mechanics"""

    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot
        
        # Set up Challonge API
        challonge_username = os.getenv('CHALLONGE_USERNAME')
        challonge_api_key = os.getenv('CHALLONGE_API_KEY')
        
        if challonge_username and challonge_api_key:
            challonge.set_credentials(challonge_username, challonge_api_key)
        else:
            log.warning("Challonge credentials not configured! Set CHALLONGE_USERNAME and CHALLONGE_API_KEY environment variables.")
        
        # Start background task
        self.process_tournaments.start()

    def cog_unload(self):
        self.process_tournaments.cancel()

    @tasks.loop(seconds=60)  # Check every minute
    async def process_tournaments(self):
        """Process active tournaments"""
        for tournament in list(active_tournaments.values()):
            if tournament.state == TournamentState.ACTIVE:
                await self._process_tournament(tournament)

    @process_tournaments.before_loop
    async def before_process_tournaments(self):
        await self.bot.wait_until_ready()

    async def _process_tournament(self, tournament: Tournament):
        """Process a single tournament"""
        try:
            # Get pending matches from Challonge
            matches = challonge.matches.index(tournament.challonge_tournament["id"])
            
            for match in matches:
                if match["state"] == "open":
                    # Find the two players
                    player1 = None
                    player2 = None
                    
                    for participant in tournament.participants:
                        if not participant.eliminated:
                            c_participant = tournament.challonge_participants.get(participant.user.id)
                            if c_participant:
                                if c_participant["id"] == match["player1_id"]:
                                    player1 = participant
                                elif c_participant["id"] == match["player2_id"]:
                                    player2 = participant
                    
                    if player1 and player2:
                        # Simulate battle
                        await self._simulate_match(tournament, match, player1, player2)
                        break  # Process one match at a time
            
            # Check if tournament is complete
            tournament_info = challonge.tournaments.show(tournament.challonge_tournament["id"])
            if tournament_info["state"] == "awaiting_review":
                challonge.tournaments.finalize(tournament.challonge_tournament["id"])
                tournament.state = TournamentState.FINISHED
                
        except Exception as e:
            log.error(f"Error processing tournament {tournament.name}: {e}")

    async def _simulate_match(self, tournament: Tournament, challonge_match: dict, player1: TournamentPlayer, player2: TournamentPlayer):
        """Simulate a match between two players"""
        try:
            # Run battle simulation
            log.info("starting")
            battle_result = simulate_tournament_battle(
                player1.balls,
                player2.balls,
                player1.user.display_name,
                player2.user.display_name
            )
            
            # Determine winner
            winner = None
            loser = None
            if battle_result['winner'] == player1.user.display_name:
                winner = player1
                loser = player2
            else:
                winner = player2
                loser = player1
            
            # Mark loser as eliminated
            loser.eliminated = True

            active_players = [p for p in tournament.participants if not p.eliminated]
            if len(active_players) == 1:
                tournament.state = TournamentState.FINISHED
                await self._handle_tournament_complete(tournament)
            
            # Update Challonge match
            winner_participant = tournament.challonge_participants.get(winner.user.id)
            if winner_participant:
                challonge.matches.update(
                    tournament.challonge_tournament["id"],
                    challonge_match["id"],
                    winner_id=winner_participant["id"],
                    scores_csv="1-0"
                )
            
            # Send match result to channel
            await self._send_match_result(tournament, winner, loser, battle_result)
            
        except Exception as e:
            log.error(f"Error simulating match: {e}")

    async def _send_match_result(self, tournament: Tournament, winner: TournamentPlayer, loser: TournamentPlayer, battle_result: dict):
        """Send match result to tournament channel"""
        try:
            channel = self.bot.get_channel(tournament.channel_id)
            if not channel:
                return
            
            embed = discord.Embed(
                title="Tournament Match Result",
                description=f"**{winner.user.display_name}** defeats **{loser.user.display_name}**",
                color=discord.Color.green()
            )
            
            embed.add_field(
                name="Battle Info",
                value=f"Turns: {battle_result['turns']}\nWinner: {battle_result['winner']}",
                inline=False
            )
            
            # Show winner's team
            if winner.balls:
                winner_team = "\n".join([f"• {ball.emoji} {ball.country}" for ball in winner.balls[:5]])
                if len(winner.balls) > 5:
                    winner_team += f"\n• ... and {len(winner.balls) - 5} more"
                embed.add_field(name="Winner's Team", value=winner_team, inline=False)
            
            # Attach battle log
            if battle_result['battle_log']:
                battle_log_text = "\n".join(battle_result['battle_log'])
                battle_file = discord.File(
                    io.StringIO(battle_log_text),
                    filename=f"battle_{winner.user.display_name}_vs_{loser.user.display_name}.txt"
                )
                await channel.send(embed=embed, file=battle_file)
            else:
                await channel.send(embed=embed)
                
        except Exception as e:
            log.error(f"Failed to send match result: {e}")

    async def _handle_tournament_complete(self, tournament: Tournament):
        """Handle tournament completion"""
        try:
            channel = self.bot.get_channel(tournament.channel_id)
            if not channel:
                return
            
            # Get final standings
            await asyncio.sleep(5)
            
            participants = challonge.participants.index(tournament.challonge_tournament["id"])
          #participants.sort(key=lambda x: x.get('final_rank', 999))
            participants.sort(key=lambda x: x.get('final_rank') or 999)
             
            # Find champion
            champion = None
            if participants:
                winner_data = participants[0]
                for user_id, c_participant in tournament.challonge_participants.items():
                    if c_participant["id"] == winner_data["id"]:
                        champion = next((p for p in tournament.participants if p.user.id == user_id), None)
                        break
            
            embed = discord.Embed(
                title=f"Tournament Complete: {tournament.name}",
                description="The tournament has concluded!",
                color=discord.Color.gold()
            )
            
            if champion:
                embed.add_field(
                    name="Tournament Winner",
                    value=f"**{champion.user.display_name}**",
                    inline=False
                )
                
                # Show winner's team
                if champion.balls:
                    champion_team = "\n".join([f"• {ball.emoji} {ball.country}" for ball in champion.balls])
                    embed.add_field(name="winner's Team", value=champion_team, inline=False)
            
            embed.add_field(
                name="Tournament Stats",
                value=f"**Participants:** {len(tournament.participants)}\n**Type:** {tournament.tournament_type.value.title()}",
                inline=False
            )
            
            if tournament.challonge_tournament:
                embed.add_field(
                    name="🔗 Full Results",
                    value=f"[View on Challonge]({tournament.challonge_tournament['full_challonge_url']})",
                    inline=False
                )
            
            await channel.send(f"🎉 **{tournament.name}** has concluded!", embed=embed)
            
            # Clean up
            if tournament.guild_id in active_tournaments:
                del active_tournaments[tournament.guild_id]
                
        except Exception as e: 
            log.error(f"error in sending results: {e}\n{traceback.format_exc()}")
              
    @app_commands.command()
    async def create(
        self,
        interaction: discord.Interaction,
        name: str,
        tournament_type: str,
        max_participants: int = 16,
        min_rarity: Optional[float] = None,
        max_rarity: Optional[float] = None,
        special_allowed: bool = True,
        duplicates_allowed: bool = False,
        balls_per_player: int = 5
    ):
        """
        Create a new tournament
        
        Parameters
        ----------
        name: str
            Tournament name
        tournament_type: str
            Type: single_elimination, double_elimination, round_robin, or swiss
        max_participants: int
            Maximum participants (default: 16)
        min_rarity: float
            Minimum ball rarity (optional)
        max_rarity: float
            Maximum ball rarity (optional)
        special_allowed: bool
            Allow special balls (default: True)
        duplicates_allowed: bool
            Allow duplicate ball types (default: False)
        balls_per_player: int
            Number of balls per player (default: 5)
        """
        
        # Check if tournament already exists in this guild
        if interaction.guild_id in active_tournaments:
            await interaction.response.send_message(
                "A tournament is already active in this server!", ephemeral=True
            )
            return
        
        # Validate tournament type
        try:
            t_type = TournamentType(tournament_type.lower().replace('_', ' '))
        except ValueError:
            valid_types = [t.value for t in TournamentType]
            await interaction.response.send_message(
                f"Invalid tournament type! Valid types: {', '.join(valid_types)}", 
                ephemeral=True
            )
            return
        
        await interaction.response.defer()
        
        # Create unique tournament ID
        tournament_id = f"{name}_{interaction.guild_id}_{random.randint(1000, 9999)}"

        slug_base = _slugify(name)
        slug = f"{slug_base}_{interaction.guild_id}_{random.randint(1000,9999)}"     
        
        # Create Challonge tournament
        try:
            challonge_tournament = challonge.tournaments.create(
                tournament_id,
                slug,
                tournament_type=t_type.value,
                description=f"Dex Tournament: {name}",
                private=True
            )
        except Exception as e:
            await interaction.followup.send(
                f"Failed to create tournament on Challonge: {e}", ephemeral=True
            )
            return
        
        # Create tournament
        tournament = Tournament(
            name=name,
            guild_id=interaction.guild_id,
            channel_id=interaction.channel_id,
            organizer=interaction.user,
            tournament_type=t_type,
            max_participants=max_participants,
            min_rarity=min_rarity,
            max_rarity=max_rarity,
            special_allowed=special_allowed,
            duplicates_allowed=duplicates_allowed,
            balls_per_player=balls_per_player,
            challonge_tournament=challonge_tournament
        )
        
        active_tournaments[interaction.guild_id] = tournament
        
        # Create registration view and embed
        view = TournamentRegistrationView(tournament)
        embed = view._create_registration_embed()
        
        await interaction.followup.send(
            f"Tournament **{name}** created!.",
            embed=embed,
            view=view
        )

    @app_commands.command()
    async def status(self, interaction: discord.Interaction):
        """Check the status of the current tournament"""
        tournament = active_tournaments.get(interaction.guild_id)
        
        if not tournament:
            await interaction.response.send_message(
                "No active tournament in this server!", ephemeral=True
            )
            return
        
        if tournament.state == TournamentState.REGISTRATION:
            view = TournamentRegistrationView(tournament)
            embed = view._create_registration_embed()
            await interaction.response.send_message(embed=embed, view=view)
        elif tournament.state == TournamentState.ACTIVE:
            embed = discord.Embed(
                title=f"{tournament.name}",
                description="Tournament is currently active! Battles are being processed automatically.",
                color=discord.Color.green()
            )
            
            # Show active participants
            active_participants = [p for p in tournament.participants if not p.eliminated]
            eliminated_participants = [p for p in tournament.participants if p.eliminated]
            
            if active_participants:
                active_list = "\n".join([f"• {p.user.display_name}" for p in active_participants[:10]])
                if len(active_participants) > 10:
                    active_list += f"\n• ... and {len(active_participants) - 10} more"
                embed.add_field(name="Still in Tournament", value=active_list, inline=False)
            
            if eliminated_participants:
                eliminated_list = "\n".join([f"• {p.user.display_name}" for p in eliminated_participants[-5:]])
                embed.add_field(name="Recently Eliminated", value=eliminated_list, inline=False)
            
            if tournament.challonge_tournament:
                embed.add_field(
                    name="📊 Tournament Bracket",
                    value=f"[View on Challonge]({tournament.challonge_tournament['full_challonge_url']})",
                    inline=False
                )
            
            await interaction.response.send_message(embed=embed)
        elif tournament.state == TournamentState.FINISHED:
            embed = discord.Embed(
                title=f"{tournament.name}",
                description="Tournament has finished!",
                color=discord.Color.gold()
            )
            
            if tournament.challonge_tournament:
                embed.add_field(
                    name="Final Results",
                    value=f"[View on Challonge]({tournament.challonge_tournament['full_challonge_url']})",
                    inline=False
                )
            
            await interaction.response.send_message(embed=embed)

    @app_commands.command()
    async def myballs(self, interaction: discord.Interaction):
        """View your balls selected for the tournament"""
        tournament = active_tournaments.get(interaction.guild_id)
        
        if not tournament:
            await interaction.response.send_message(
                "No active tournament in this server!", ephemeral=True
            )
            return
        
        # Find participant
        participant = next((p for p in tournament.participants if p.user.id == interaction.user.id), None)
        
        if not participant:
            await interaction.response.send_message(
                "You are not registered for this tournament!", ephemeral=True
            )
            return
        
        if not participant.balls:
            await interaction.response.send_message(
                "Balls have not been auto-selected yet! Tournament must be started first.", ephemeral=True
            )
            return
        
        embed = discord.Embed(
            title=f"Your Tournament Team",
            description=f"Selected for **{tournament.name}**",
            color=discord.Color.blue()
        )
        
        # Show team
        balls_text = "\n".join([f"• {ball.emoji} {ball.country} (HP: {ball.health} | ATK: {ball.attack})" for ball in participant.balls])
        embed.add_field(name="Your Team", value=balls_text, inline=False)
        
        # Calculate total stats
        total_health = sum(ball.health for ball in participant.balls)
        total_attack = sum(ball.attack for ball in participant.balls)
        embed.add_field(name="Total Stats", value=f"HP: {total_health} | ATK: {total_attack}", inline=False)
        
        # Show status
        status = "Eliminated" if participant.eliminated else "✅ Still in tournament"
        embed.add_field(name="Status", value=status, inline=False)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command()
    async def cancel(self, interaction: discord.Interaction):
        """Cancel the current tournament (organizer only)"""
        tournament = active_tournaments.get(interaction.guild_id)
        
        if not tournament:
            await interaction.response.send_message(
                "No active tournament in this server!", ephemeral=True
            )
            return
        
        if interaction.user.id != tournament.organizer.id:
            await interaction.response.send_message(
                "Only the tournament organizer can cancel the tournament!", ephemeral=True
            )
            return
        
        # Cancel Challonge tournament
        try:
            challonge.tournaments.destroy(tournament.challonge_tournament["id"])
        except Exception as e:
            log.error(f"Failed to cancel Challonge tournament: {e}")
        
        tournament.state = TournamentState.CANCELLED
        
        embed = discord.Embed(
            title=f"❌ Tournament Cancelled",
            description=f"**{tournament.name}** has been cancelled by the organizer.",
            color=discord.Color.red()
        )
        
        await interaction.response.send_message(embed=embed)
        
        # Remove from active tournaments
        if tournament.guild_id in active_tournaments:
            del active_tournaments[tournament.guild_id]


