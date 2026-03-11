import random

EXPERIMENTS = 10_000
FLIPS_PER_EXPERIMENT = 100
STREAK_LENGTH = 6


def run_experiments(num_experiments: int, flips_per: int, streak_len: int) -> int:
    """Run experiments and return the number that contained a streak."""
    number_of_streaks = 0
    for _ in range(num_experiments):
        count = 0
        last = None
        for _ in range(flips_per):
            flip = random.randint(0, 1)
            count = count + 1 if flip == last else 1
            last = flip
            if count == streak_len:
                number_of_streaks += 1
                break
    return number_of_streaks


if __name__ == '__main__':
    streaks = run_experiments(EXPERIMENTS, FLIPS_PER_EXPERIMENT, STREAK_LENGTH)
    print(f'Chance of streak: {streaks / EXPERIMENTS * 100:.2f}%')
