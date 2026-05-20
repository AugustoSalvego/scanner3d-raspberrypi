import cv2

camera = cv2.VideoCapture(0)

while True:

    success, frame = camera.read()

    if success:
        cv2.imshow(
            "Test Camera",
            frame
        )

    key = cv2.waitKey(1)

    if key == 27:
        break

camera.release()
cv2.destroyAllWindows()