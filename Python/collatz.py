def collatz(number):
    if number % 2:
        return_value = 3 * number +1

    else:
        return_value = number // 2
    print(return_value, end='')
    return(return_value)

print('Input a integer number')
print('This programme will calculate until it gets 1!')
try:
    cal_until_one = int(input('>'))
    while cal_until_one != 1:
        cal_until_one = collatz(cal_until_one)
        print(' ', end='')
    print()
except ValueError:
    print('You must enter an integer')
