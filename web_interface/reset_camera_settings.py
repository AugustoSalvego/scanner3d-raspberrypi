import cv2
import time

camera = cv2.VideoCapture(0)

settings = {
    cv2.CAP_PROP_BRIGHTNESS: 0,
    cv2.CAP_PROP_CONTRAST: 0,
    cv2.CAP_PROP_SATURATION: 0,
    cv2.CAP_PROP_SHARPNESS: 0,
    cv2.CAP_PROP_EXPOSURE: -6,
    cv2.CAP_PROP_GAIN: 0,
}

for prop, value in settings.items():
    camera.set(prop, value)
    time.sleep(0.1)

print("Camera settings reset attempted.")

camera.release()