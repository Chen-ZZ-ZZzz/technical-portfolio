def collatz(number):
    """
    Compute the next number in the Collatz sequence.
    If number is odd: 3n + 1
    If number is even: n / 2
    """
    if number % 2 == 1:
        next_number = 3 * number + 1
    else:
        next_number = number // 2
    print(next_number, end=' ')
    return next_number

def main():
    print('Input an integer number:')
    print('This program will calculate the Collatz sequence until it reaches 1!')
    try:
        number = int(input('> '))
        while number != 1:
            number = collatz(number)
        print()  # for newline at the end
    except ValueError:
        print('Error: You must enter an integer.')

if __name__ == "__main__":
    main()
