import random
import logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

guess = ''
while guess not in ('heads', 'tails'):
    print('Guess the coin toss! Enter heads or tails:')
    guess = input()
logging.info('guess is ' + guess)
if random.randint(0, 1):
    toss = 'heads'    # 0 is tails, 1 is heads
else:
    toss = 'tails'
logging.info('toss is ' + toss)
if toss == guess:
    print('You got it!')
else:
    print('Nope! Guess again!')
    guess=''
    while guess not in ('heads', 'tails'):
        print('Enter heads or tails:')
        guess = input()
    if toss == guess:
        print('You got it!')
    else:
        print('Nope. You are really bad at this game.')
