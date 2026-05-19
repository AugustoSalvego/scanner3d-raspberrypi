from gpiozero import LED
from time import sleep

pins = [17, 27, 22, 23]

leds = [LED(pin) for pin in pins]

print("Testando GPIOs...")

while True:
    for led in leds:
        led.on()
        sleep(0.5)
        led.off()
