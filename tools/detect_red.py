import cv2
import numpy as np

image = cv2.imread("opencv_hd.jpg")

if image is None:
    print("Erro: não encontrei opencv_hd.jpg")
    exit()

hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

lower_red1 = np.array([0, 80, 80])
upper_red1 = np.array([10, 255, 255])

lower_red2 = np.array([170, 80, 80])
upper_red2 = np.array([180, 255, 255])

mask1 = cv2.inRange(hsv, lower_red1, upper_red1)
mask2 = cv2.inRange(hsv, lower_red2, upper_red2)

mask = mask1 + mask2

result = cv2.bitwise_and(image, image, mask=mask)

cv2.imwrite("red_mask.jpg", mask)
cv2.imwrite("red_detected.jpg", result)

print("Arquivos gerados:")
print("- red_mask.jpg")
print("- red_detected.jpg")
