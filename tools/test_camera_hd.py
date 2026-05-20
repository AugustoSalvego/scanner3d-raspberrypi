import cv2

camera = cv2.VideoCapture(0)

camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

if not camera.isOpened():
    print("Erro: câmera não abriu")
    exit()

for i in range(10):
    ret, frame = camera.read()

if ret:
    print("Resolução capturada:", frame.shape)
    cv2.imwrite("opencv_hd.jpg", frame)
    print("Imagem salva como opencv_hd.jpg")
else:
    print("Erro: não conseguiu capturar imagem")

camera.release()
