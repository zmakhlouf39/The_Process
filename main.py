#fizzbuzz cuz twitter said it was easy and i checked O(n) is optimal
def Fizzbuzz(n):
    text =""
    for i in range(n):
        if(i%3==0):
            text += "Fizz"
        if(i%5==0):
            text += "Buzz"
    return text

def main():
    n = 10
    print(Fizzbuzz(n))

if __name__ == "__main__":
    main()
