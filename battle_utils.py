# ballsdex/packages/tournament/battle_utils.py
from dataclasses import dataclass, field
import random


@dataclass
class BattleBall:
    name: str
    owner: str
    health: int
    attack: int
    emoji: str = ""
    dead: bool = False


@dataclass
class BattleInstance:
    p1_balls: list = field(default_factory=list)
    p2_balls: list = field(default_factory=list)
    winner: str = ""
    turns: int = 0


def get_damage(ball):
    return int(ball.attack * random.uniform(0.8, 1.2))


def attack(current_ball, enemy_balls):
    alive_balls = [ball for ball in enemy_balls if not ball.dead]
    enemy = random.choice(alive_balls)

    attack_dealt = get_damage(current_ball)
    enemy.health -= attack_dealt

    if enemy.health <= 0:
        enemy.health = 0
        enemy.dead = True
    
    if enemy.dead:
        gen_text = f"{current_ball.owner}'s {current_ball.name} has killed {enemy.owner}'s {enemy.name}"
    else:
        gen_text = f"{current_ball.owner}'s {current_ball.name} has dealt {attack_dealt} damage to {enemy.owner}'s {enemy.name}"
    return gen_text


def random_events():
    if random.randint(0, 100) <= 30:  # 30% miss chance
        return 1
    else:
        return 0


def gen_battle(battle: BattleInstance):
    """Generate battle between two players"""
    turn = 0

    # Check if all balls do no damage
    if all(ball.attack <= 0 for ball in battle.p1_balls + battle.p2_balls):
        yield "Everyone stared at each other, resulting in nobody winning."
        return

    while any(ball for ball in battle.p1_balls if not ball.dead) and any(
        ball for ball in battle.p2_balls if not ball.dead
    ):
        alive_p1_balls = [ball for ball in battle.p1_balls if not ball.dead]
        alive_p2_balls = [ball for ball in battle.p2_balls if not ball.dead]

        for p1_ball, p2_ball in zip(alive_p1_balls, alive_p2_balls):
            # Player 1 attacks first
            if not p1_ball.dead:
                turn += 1

                event = random_events()
                if event == 1:
                    yield f"Turn {turn}: {p1_ball.owner}'s {p1_ball.name} missed {p2_ball.owner}'s {p2_ball.name}"
                    continue
                yield f"Turn {turn}: {attack(p1_ball, battle.p2_balls)}"

                if all(ball.dead for ball in battle.p2_balls):
                    break

            # Player 2 attacks
            if not p2_ball.dead:
                turn += 1

                event = random_events()
                if event == 1:
                    yield f"Turn {turn}: {p2_ball.owner}'s {p2_ball.name} missed {p1_ball.owner}'s {p1_ball.name}"
                    continue
                yield f"Turn {turn}: {attack(p2_ball, battle.p1_balls)}"

                if all(ball.dead for ball in battle.p1_balls):
                    break

    # Determine the winner
    if all(ball.dead for ball in battle.p1_balls):
        battle.winner = battle.p2_balls[0].owner
    elif all(ball.dead for ball in battle.p2_balls):
        battle.winner = battle.p1_balls[0].owner

    # Set turns
    battle.turns = turn


def simulate_tournament_battle(player1_balls, player2_balls, player1_name, player2_name):
    """Simulate a battle between two tournament players"""
    # Convert tournament balls to battle balls
    p1_battle_balls = []
    for ball in player1_balls:
        battle_ball = BattleBall(
            name=ball.country,
            owner=player1_name,
            health=ball.health,
            attack=ball.attack,
            emoji=ball.emoji
        )
        p1_battle_balls.append(battle_ball)
    
    p2_battle_balls = []
    for ball in player2_balls:
        battle_ball = BattleBall(
            name=ball.country,
            owner=player2_name,
            health=ball.health,
            attack=ball.attack,
            emoji=ball.emoji
        )
        p2_battle_balls.append(battle_ball)
    
    # Create battle instance
    battle = BattleInstance(
        p1_balls=p1_battle_balls,
        p2_balls=p2_battle_balls
    )
    
    # Generate battle log
    battle_log = list(gen_battle(battle))
    
    return {
        'winner': battle.winner,
        'turns': battle.turns,
        'battle_log': battle_log
    }
