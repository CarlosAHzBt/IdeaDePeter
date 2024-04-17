import cv2 as cv
import numpy as np
import os
from ConvertirPixelesAMetros import ConvertirPixelesAMetros
from FiltrosDeProcesamiento import PointCloudFilter
from Ransac import RANSAC
from ObtenerAlturaDeCaptura import AlturaCaptura
import open3d as o3d
from AdministradorDeArchivos import AdministradorArchivos

class Bache:
    def __init__(self, bag_de_origen, ruta_bag, ruta_imagenRGB, id_bache, coordenadas=None):
        self.id_bache = id_bache
        self.bag_de_origen = bag_de_origen
        self.ruta_bag = ruta_bag
        self.ruta_imagenRGB = ruta_imagenRGB
        self.imagen_original_shape = (480, 848)  # Resolution de la imagen
        self.coordenadas = np.array(coordenadas) if coordenadas is not None else np.empty((0, 2), dtype=int)
        
        self.convPx2M = ConvertirPixelesAMetros()
        self.pointCloudFilter = PointCloudFilter()
        self.ransac = RANSAC()

    def procesar_bache(self):
        self.calcular_contorno()
        if not self.contorno.size:
            return False
        altura_captura = self.estimar_altura_captura()
        self.escala_horizontal, _ = self.convPx2M.calcular_escala(altura_captura)
        pcd_cropped = self.recortar_y_procesar_nube_de_puntos()
        if pcd_cropped:
            return self.estimar_profundidad_del_bache(pcd_cropped)
        return False

    def calcular_contorno(self):
        if self.coordenadas.size == 0:
            raise ValueError("No hay coordenadas para calcular el contorno.")
        mask = np.zeros(self.imagen_original_shape[:2], dtype=np.uint8)
        for x, y in self.coordenadas:
            mask[x ,y] = 255
        contornos, _ = cv.findContours(mask, cv.RETR_EXTERNAL, cv.CHAIN_APPROX_NONE)
        contorno_externo = max(contornos, key=cv.contourArea).squeeze()
        if contorno_externo.ndim == 1:
            contorno_externo = contorno_externo.reshape(-1, 1, 2)
        self.contorno = contorno_externo

    def generar_imagen_con_contorno_y_circulo(self):
        imagen = cv.imread(self.ruta_imagenRGB)
        if imagen is not None:
            cv.drawContours(imagen, [self.contorno], -1, (0, 255, 0), 2)  # Dibuja el contorno en verde
            if hasattr(self, 'centro_circulo') and hasattr(self, 'radio_maximo'):
                try:
                    cv.circle(imagen, self.centro_circulo, int(self.radio_circulo_bache_px), (0, 255, 0), 2)  # Dibuja el círculo en verde
                except TypeError as e:
                    print(f"Error al dibujar el círculo: {e}")
            # Define la ruta para guardar la imagen
            ruta_imagen_con_dibujos = f"{self.bag_de_origen}\{self.id_bache}_marked_image.png"
            administradorDeArchivos = AdministradorArchivos()
            administradorDeArchivos.crear_carpeta(f"Resultados/imagenesContorno")
            administradorDeArchivos.crear_carpeta(f"Resultados/imagenesContorno/{self.bag_de_origen}")
            administradorDeArchivos.imagenes_contorno_bache(("Resultados/imagenesContorno/"+ruta_imagen_con_dibujos), imagen)
            self.ruta_imagen_contorno = "../Resultados/imagenesContorno/"+ruta_imagen_con_dibujos
           
            return ruta_imagen_con_dibujos
        else:
            print("No se pudo cargar la imagen.")
            return None

    def estimar_altura_captura(self):
        ply_path = os.path.join(self.bag_de_origen, "ply", f"{self.id_bache.rsplit('_', 1)[0]}.ply")
        return AlturaCaptura(ply_path).calcular_altura()
    
    def set_depth_image(self):
        nombre_archivo_sin_extension = self.id_bache[:-2]
        ruta_depth_image = os.path.join(self.bag_de_origen, "ImagenesProfundidad", f"{nombre_archivo_sin_extension}.png")
        if os.path.exists(ruta_depth_image):
            self.ruta_depth_image = cv.imread(ruta_depth_image, cv.IMREAD_ANYDEPTH)
        else:
            raise FileNotFoundError(f"No se encontró la imagen de profundidad para {self.ruta_imagenRGB}")
        return self.ruta_depth_image

    def recortar_y_procesar_nube_de_puntos(self):
        depth_image = self.set_depth_image()
        pipeline = self.pointCloudFilter.start_pipeline(self.ruta_bag)
        intrinsecos, depth_scale = self.pointCloudFilter.obtener_intrinsecos_from_pipeline(pipeline)
        pcd = self.pointCloudFilter.depth_image_to_pointcloud(depth_image, intrinsecos, depth_scale)
        
        #o3d.visualization.draw_geometries([pcd])
        self.radio_maximo = self.calcular_radio_maximo()
        if self.diametro_bache < 130:
            return None
        
        bounding_box = self.pointCloudFilter.get_bounding_box(self.contorno)
        pcd, R = self.ransac.segmentar_plano_y_nivelar(pcd)
        # Calcular la moda de las alturas en el eje Z
        puntos = np.asarray(pcd.points)
        self.altura_captura = np.median(puntos[:, 2])
        #o3d.visualization.draw_geometries([pcd])
        pcd_cropped = self.pointCloudFilter.recortar_nube_de_puntos(pcd, intrinsecos, depth_image, bounding_box, depth_scale, R, (0,0,0))
        return pcd_cropped


    
    def calcular_radio_maximo(self):
        if len(self.contorno) == 0:
            raise ValueError("El contorno debe ser calculado antes de calcular el radio máximo.")
        imagen_contorno = np.zeros(self.imagen_original_shape[:2], dtype=np.uint8)
        cv.drawContours(imagen_contorno, [self.contorno], -1, color=255, thickness=-1)
        puntos_dentro_del_contorno = np.argwhere(imagen_contorno == 255)
        self.radio_maximo = 0
        for punto in puntos_dentro_del_contorno:
            dist = cv.pointPolygonTest(self.contorno, (int(punto[1]), int(punto[0])), True)
            if dist > self.radio_maximo:
                self.radio_maximo = dist
                self.centro_circulo = (int(punto[1]), int(punto[0]))
        if self.radio_maximo == 0:
            raise ValueError("No se encontraron puntos dentro del contorno para calcular el radio máximo.")
        self.radio_circulo_bache_px = self.radio_maximo    
        self.radio_maximo = self.convPx2M.convertir_radio_pixeles_a_metros(self.radio_maximo, self.escala_horizontal)
        self.radio_maximo *= 1000
        self.diametro_bache = self.radio_maximo * -2

        print(f"el diametro del bache es de {self.diametro_bache} mm")



    def estimar_profundidad_del_bache(self, pcd_cropped):
        puntos = np.asarray(pcd_cropped.points)

        imagenControno = self.generar_imagen_con_contorno_y_circulo()
        #Guardar la imagen en la carpeta de resultados
        
        #ver nube de puntos
        #o3d.visualization.draw_geometries([pcd_cropped])
        z = puntos[:, 2]

        profundidad = self.altura_captura - np.max(z) 
        print(f"La profundidad del bache {self.id_bache} es de {profundidad} m, con una altura de captura de {self.altura_captura} m.")
        self.profundidad_del_bache_estimada = profundidad
        return profundidad