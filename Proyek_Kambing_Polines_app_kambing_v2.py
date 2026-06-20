import subprocess
import sys

# --- SURGICAL PATCH: BYPASS OS ERROR ---
# Mencegat error libGL/libgthread dengan memaksa server membuang 
# OpenCV berat dan menggantinya dengan versi headless secara otomatis.
try:
    import cv2
except ImportError:
    subprocess.run([sys.executable, "-m", "pip", "uninstall", "-y", "opencv-python", "opencv-python-headless"])
    subprocess.run([sys.executable, "-m", "pip", "install", "opencv-python-headless"])
    import cv2
# ---------------------------------------

import streamlit as st
import numpy as np
from ultralytics import YOLO
from streamlit_webrtc import webrtc_streamer, VideoProcessorBase, RTCConfiguration
import av
import threading

# 1. Pengaturan Halaman & Desain Dashboard
st.set_page_config(page_title="Estimasi Berat Badan Kambing - WebRTC", layout="wide")

st.title("⚖️ Estimasi Berat Badan Kambing")
st.caption("Aplikasi Teknologi Fotogrametri Digital | Tim Peneliti Politeknik Negeri Semarang")

# Konfigurasi STUN Server agar WebRTC berjalan stabil di jaringan publik (Internet)
# Fungsi dinamis mengambil pelayan relai TURN dari Twilio
@st.cache_resource
def get_ice_servers():
    try:
        account_sid = st.secrets["TWILIO_ACCOUNT_SID"]
        auth_token = st.secrets["TWILIO_AUTH_TOKEN"]
        client = Client(account_sid, auth_token)
        token = client.tokens.create()
        return token.ice_servers
    except Exception as e:
        st.warning("Gagal menyambung ke Twilio. Menggunakan fallback STUN.")
        return [{"urls": ["stun:stun.l.google.com:19302"]}]

RTC_CONFIGURATION = RTCConfiguration({"iceServers": get_ice_servers()})

# 2. Load Model AI (Menggunakan Cache agar tidak reload terus-menerus)
@st.cache_resource
def load_model():
    # Menggunakan model hasil training terbaik Anda
    return YOLO("best.pt")

try:
    model = load_model()
except Exception as e:
    st.error(f"Gagal memuat file model 'best.pt'. Pastikan file tersebut berada di folder yang sama dengan script ini. Detail: {e}")

# 3. Class Video Processor untuk Mengolah Stream Kamera Klient

class GoatVideoProcessor(VideoProcessorBase):
    def __init__(self):
        self.lock = threading.Lock()
        self.panjang_cm = 0.0
        self.tinggi_cm = 0.0
        self.luas_px = 0.0
        self.berat_kg = 0.0
        # --- KODE BARU: Memori untuk 10 frame terakhir ---
        self.buffer_berat = [] 
        self.buffer_panjang = []
        self.buffer_tinggi = []
        
    def recv(self, frame: av.VideoFrame) -> av.VideoFrame:
        # Konversi frame WebRTC ke matriks gambar OpenCV (BGR)
        img = frame.to_ndarray(format="bgr24")
        
        # Jalankan inferensi YOLOv8 Segmentasi dengan batas confidence 15%
        hasil_deteksi = model(img, conf=0.15, stream=True)
        
        luas_area_total = 0
        lebar_px_total = 0
        height_px_total = 0
        
        for hasil in hasil_deteksi:
            # Gambar poligon segmentasi biru (tanpa label bawaan pabrik)
            img = hasil.plot(labels=False)
            
            if hasil.boxes:
                for box in hasil.boxes:
                    lebar_px = box.xywh[0][2].item()
                    tinggi_px = box.xywh[0][3].item()
                    
                    # Akumulasi penjumlahan multi-objek
                    luas_area_total += (lebar_px * tinggi_px)
                    lebar_px_total += lebar_px
                    height_px_total += tinggi_px
                    
                    # Ambil koordinat untuk menggambar teks kustom skala puluhan (contoh: 32.0 atau 99.0)
                    x1, y1, x2, y2 = box.xyxy[0]
                    nilai_skala = box.conf[0].item() * 100
                    teks_kustom = f"Kambing {nilai_skala:.1f}"
                    
                    cv2.putText(img, teks_kustom, (int(x1), int(y1) - 10), 
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        
        # --- RUMUS KONVERSI FOTOGRAMETRI ---
        faktor_kalibrasi_panjang = 0.1026
        faktor_kalibrasi_tinggi = 0.1293
        
        panjang_cm = lebar_px_total * faktor_kalibrasi_panjang
        tinggi_cm = height_px_total * faktor_kalibrasi_tinggi
        
        if luas_area_total > 0:
            estimasi_berat_kg = (luas_area_total * 0.00015) + 5
        else:
            estimasi_berat_kg = 0.0

        # --- KODE BARU: Logika Moving Average ---
        self.buffer_berat.append(estimasi_berat_kg)
        self.buffer_panjang.append(panjang_cm)
        self.buffer_tinggi.append(tinggi_cm)
        
        # Jaga agar memori tidak lebih dari 10 frame (sekitar 2 detik peredaman)
        if len(self.buffer_berat) > 10:
            self.buffer_berat.pop(0)
            self.buffer_panjang.pop(0)
            self.buffer_tinggi.pop(0)
            
        # Hitung nilai rata-rata yang stabil
        berat_stabil = sum(self.buffer_berat) / len(self.buffer_berat)
        panjang_stabil = sum(self.buffer_panjang) / len(self.buffer_panjang)
        tinggi_stabil = sum(self.buffer_tinggi) / len(self.buffer_tinggi)
            
        # --- DESAIN OVERLAY HUD (HEADS-UP DISPLAY) TEKNIS PADA VIDEO ---
        # Membuat kotak semi-transparan di pojok kiri atas video agar pembacaan data di lapangan sangat mudah
        overlay = img.copy()
        cv2.rectangle(overlay, (15, 15), (340, 145), (35, 35, 35), -1)
        cv2.addWeighted(overlay, 0.65, img, 0.35, 0, img)
        
        # Teks parameter pengukuran lapangan
        cv2.putText(img, "DIGITAL PHOTOGRAMMETRY HUD", (25, 38), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 2)
        cv2.putText(img, f"Total Panjang : {panjang_stabil:.1f} cm", (25, 65), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.putText(img, f"Total Tinggi  : {tinggi_stabil:.1f} cm", (25, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
        cv2.putText(img, f"Estimasi Berat: {berat_stabil:.2f} kg", (25, 122), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)
        
        # Kembalikan frame video yang sudah diproses AI ke layar client
        return av.VideoFrame.from_ndarray(img, format="bgr24")

# 4. Pembagian Kolom Layout Streamlit
col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("📷 Monitor Kamera Real-Time (WebRTC)")
    st.info("Klik tombol 'Start' di bawah untuk mengaktifkan kamera perangkat Anda secara langsung melalui browser.")
    
    # Inisialisasi komponen WebRTC Streamer
    ctx = webrtc_streamer(
        key="goat-photogrammetry",
        video_processor_factory=GoatVideoProcessor,
        rtc_configuration=RTC_CONFIGURATION, # <-- Ubah baris ini saja
        media_stream_constraints={"video": True, "audio": False},
        async_processing=True,
    )

with col2:
    st.subheader("📋 Informasi Sistem & Cloud")
    st.markdown("""
    ### Panduan Deployment Cloud:
    1. Pastikan file **`best.pt`** sudah dimasukkan ke repositori GitHub Anda.
    2. Sertakan file **`requirements.txt`** di dalam repositori.
    3. Saat mendeploy di Streamlit Community Cloud, gunakan opsi **Python 3.9** atau yang lebih tinggi.
    
    ### Catatan Keamanan Protokol:
    Kamera hanya akan aktif jika domain web Anda berjalan di atas protokol aman **HTTPS**. Streamlit Community Cloud secara otomatis sudah menyediakan enkripsi HTTPS gratis (`https://...streamlit.app`).
    """)
    
    st.success("Sistem siap menerima koneksi streaming multi-client.")
