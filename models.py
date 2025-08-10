from __future__ import annotations

from typing import Optional, List
from dataclasses import dataclass, field
from enum import Enum

import discord

class TournamentType(Enum):
    SINGLE_ELIMINATION = "single elimination"
    DOUBLE_ELIMINATION = "double elimination"
    ROUND_ROBIN = "round robin"
    SWISS = "swiss"


class TournamentState(Enum):
    REGISTRATION = "registration"
    ACTIVE = "active"
    FINISHED = "finished"
    CANCELLED = "cancelled"


@dataclass
class TournamentPlayer:
    """Represents a player in the tournament"""
    user: discord.Member
    player_id: int
    balls: List = field(default_factory=list)  # Will hold BattleBall objects
    eliminated: bool = False


@dataclass
class Tournament:
    """Simplified tournament object"""
    name: str
    guild_id: int
    channel_id: int
    organizer: discord.Member
    tournament_type: TournamentType
    max_participants: int
    
    # Ball selection criteria
    min_rarity: Optional[float] = None
    max_rarity: Optional[float] = None
    special_allowed: bool = True
    duplicates_allowed: bool = False
    balls_per_player: int = 5
    
    # Tournament state
    state: TournamentState = TournamentState.REGISTRATION
    participants: List[TournamentPlayer] = field(default_factory=list)
    
    # Challonge integration
    challonge_tournament: Optional[dict] = None
    challonge_participants: dict = field(default_factory=dict) 
