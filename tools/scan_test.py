import cv2
from gpiozero import OutputDevice
from time import sleep
import os

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

def rotate_steps(steps, delay=0.002):
    for _ in range(steps):
        for step in sequence:
            set_step(step)
            sleep(delay)

camera = cv2.VideoCapture(0)

camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

os.makedirs("captures", exist_ok=True)

try:
    for i in range(10):

        print(f"Captura {i}")

        # gira plataforma
        rotate_steps(128)

        # espera estabilizar
        sleep(2)

        # limpa frames antigos do buffer
        for _ in range(20):
            camera.read()

        # captura frame atualizado
        ret, frame = camera.read()

        if ret:
            filename = f"captures/frame_{i:03d}.jpg"
            cv2.imwrite(filename, frame)
            print(f"Salvo: {filename}")
        else:
            print("Erro na captura")

finally:
    camera.release()
    release_motor()
    print("Finalizado.")
