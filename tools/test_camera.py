import cv2

camera = cv2.VideoCapture(0)

if not camera.isOpened():
	print("Erro: Camera nao abriu")
	exit()

ret, frame = camera.read()

if ret:
	cv2.imwrite("opencv_test.jpg", frame)
	print("Imagem salva como opencv_test.jpg")
else:
	print("Erro: nao conseguiu capturar imagem")

camera.release()
