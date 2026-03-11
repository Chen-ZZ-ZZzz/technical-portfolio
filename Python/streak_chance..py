import random

number_of_streaks = 0

for experiment_number in range(10000):
    results = []
    counter_0 = 0
    counter_1 = 0

    for i in range(0, 100):
        results.append(random.randint(0, 1))

    for result in results:
        if result == 0:
            counter_0 += 1
            counter_1 = 0

            if counter_0 == 6:
                number_of_streaks += 1
                break

        else:
            counter_1 += 1
            counter_0 = 0

            if counter_1 == 6:
                number_of_streaks += 1
                break

print('Chance of streak: %s%%' % (number_of_streaks / 100)
