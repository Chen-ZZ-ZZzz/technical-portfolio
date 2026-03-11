import random

number_of_streaks = 0

for experiment_number in range(10000):  # Run 100,000 experiments total.

    # Code that creates a list of 100 'heads' or 'tails' values
    countcoin = ''
    for i in range(100):       # flip coins 100 times
        if random.randint(0, 1) == 0: # tails
            countcoin += 'T'
        else:                   # heads
            countcoin += 'H'

    # Code that checks if there is a streak of 6 heads or tails in a row
    countsame = 1               # It's also easy to make an off-by-one error because remember that an H or T by itself is a streak of length 1, not of length 0.
    for i in range(99):
        if countcoin[i] == countcoin[i+1]:
            countsame += 1
        else:
            countsame = 1
        if countsame == 6:
            number_of_streaks += 1

print('Chance of streak: %s%%' % (number_of_streaks / 100))
