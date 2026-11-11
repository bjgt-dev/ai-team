"""Iteration 4: i+1 ladder of words, phrases and short sentences.

This is the engine's internal prompt progression, not a lesson and not a
vocabulary class. The child never sees a list, never gets a grammar rule,
never gets tested. The device just keeps the chat going one small step
ahead of what the child already said (i+1):

  level 0  words          colours / sizes / shapes / everyday nouns
  level 1  two-word phrases   colour/size + noun
  level 2  short sentences    I like ... / The ... is ...
  level 3  little chat        short questions the child can answer
                               with a word, a phrase or a sentence

Advancement is by speaking, never by correctness. Every prompt, visual
and gesture cue is English-only.
"""

LEVELS = [
    {
        "name": "words",
        "items": [
            {"text": "Red!", "visual": "picture: red colour card", "gesture": "hold up red card"},
            {"text": "Blue!", "visual": "picture: blue colour card", "gesture": "hold up blue card"},
            {"text": "Yellow!", "visual": "picture: yellow colour card", "gesture": "hold up yellow card"},
            {"text": "Green!", "visual": "picture: green colour card", "gesture": "hold up green card"},
            {"text": "Big!", "visual": "picture: big ball next to small ball", "gesture": "arms wide"},
            {"text": "Small!", "visual": "picture: small ball next to big ball", "gesture": "hands close together"},
            {"text": "Circle!", "visual": "picture: circle", "gesture": "draw a circle in the air"},
            {"text": "Square!", "visual": "picture: square", "gesture": "draw a square in the air"},
            {"text": "Triangle!", "visual": "picture: triangle", "gesture": "draw a triangle in the air"},
            {"text": "Apple!", "visual": "picture: red apple", "gesture": "point to the apple"},
            {"text": "Ball!", "visual": "picture: ball", "gesture": "point to the ball"},
            {"text": "Cat!", "visual": "picture: cat", "gesture": "point to the cat"},
            {"text": "Dog!", "visual": "picture: dog", "gesture": "point to the dog"},
        ],
    },
    {
        "name": "phrases",
        "items": [
            {"text": "Red apple!", "visual": "picture: red apple", "gesture": "point to the apple"},
            {"text": "Big ball!", "visual": "picture: big ball", "gesture": "point to the ball"},
            {"text": "Blue car!", "visual": "picture: blue car", "gesture": "point to the car"},
            {"text": "Small cat!", "visual": "picture: small cat", "gesture": "point to the cat"},
            {"text": "Yellow sun!", "visual": "picture: yellow sun", "gesture": "point to the sun"},
            {"text": "Green frog!", "visual": "picture: green frog", "gesture": "point to the frog"},
            {"text": "Big dog!", "visual": "picture: big dog", "gesture": "point to the dog"},
            {"text": "Small bird!", "visual": "picture: small bird", "gesture": "point to the bird"},
        ],
    },
    {
        "name": "sentences",
        "items": [
            {"text": "I like red apples.", "visual": "picture: red apples", "gesture": "point to the apples"},
            {"text": "The ball is big.", "visual": "picture: big ball", "gesture": "arms wide"},
            {"text": "The cat is small.", "visual": "picture: small cat", "gesture": "hands close together"},
            {"text": "I see a blue car.", "visual": "picture: blue car", "gesture": "point to the car"},
            {"text": "The sun is yellow.", "visual": "picture: yellow sun", "gesture": "point to the sun"},
            {"text": "The dog is big.", "visual": "picture: big dog", "gesture": "arms wide"},
        ],
    },
    {
        "name": "chat",
        "items": [
            {"text": "What colour is it?", "visual": "picture: red apple", "gesture": "point to the apple"},
            {"text": "Do you like apples?", "visual": "picture: apples", "gesture": "point to the apples"},
            {"text": "What is this?", "visual": "picture: ball", "gesture": "point to the ball"},
            {"text": "Is it big?", "visual": "picture: big ball", "gesture": "arms wide"},
            {"text": "Where is the cat?", "visual": "picture: cat", "gesture": "look around"},
        ],
    },
]


def prompt_for(level, index):
    """Item at *level*, cycling through the level's items by *index*."""
    items = LEVELS[level]["items"]
    return items[index % len(items)]


def level_name(level):
    return LEVELS[level]["name"]


def level_count():
    return len(LEVELS)
