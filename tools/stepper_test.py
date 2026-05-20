from gpiozero import OutputDevice
from time import sleep

pins = [
    OutputDevice(17),
    OutputDevice(27),
    OutputDevice(22),
    OutputDevice(23),
]

sequence = [
    [1,0,0,0],
    [1,1,0,0],
    [0,1,0,0],
    [0,1,1,0],
    [0,0,1,0],
    [0,0,1,1],
    [0,0,0,1],
    [1,0,0,1],
]

def set_step(step):
    for pin, value in zip(pins, step):
        pin.on() if value else pin.off()

def release_motor():
    for pin in pins:
        pin.off()

def rotate_steps(steps, delay=0.002, direction=1):
    seq = sequence if direction == 1 else list(reversed(sequence))

    for _ in range(steps):
        for step in seq:
            set_step(step)
            sleep(delay)

try:
    print("Girando meia volta aproximada...")
    rotate_steps(256, delay=0.002, direction=1)
    print("Parou.")
finally:
    release_motor()
    print("Bobinas desligadas.")
