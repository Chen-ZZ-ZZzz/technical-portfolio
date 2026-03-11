import random
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def get_guess(prompt: str) -> str:
    """Prompt the user until they enter a valid guess."""
    while True:
        print(prompt)
        guess = input().strip().lower()
        if guess in ('heads', 'tails'):
            return guess
        print("Invalid input. Please enter 'heads' or 'tails'.")

def flip_coin() -> str:
    """Simulate a coin toss."""
    return 'heads' if random.randint(0, 1) else 'tails'

def play_game():
    toss = flip_coin()
    logging.info(f'Toss result: {toss}')

    guess = get_guess('Guess the coin toss! Enter heads or tails:')
    logging.info(f'First guess: {guess}')

    if toss == guess:
        print('You got it!')
        return

    print('Nope! Guess again!')
    guess = get_guess('Enter heads or tails:')
    logging.info(f'Second guess: {guess}')

    if toss == guess:
        print('You got it!')
    else:
        print('Nope. You are really bad at this game.')

if __name__ == '__main__':
    play_game()
