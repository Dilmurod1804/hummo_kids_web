# face_utils.py
# Xodimlar uchun yuz taqqoslash moduli.
#
# Ishlash tartibi (avtomatik tanlaydi):
#   1. deepface  — agar o'rnatilgan bo'lsa (eng aniq)
#   2. OpenCV    — agar o'rnatilgan bo'lsa (Haar Cascade + histogram)
#   3. Fallback  — hech biri yo'q => REJECT (xavfsizlik sababli)

import io
import os
import logging
import base64
import tempfile
from typing import Tuple

logger = logging.getLogger(__name__)

# ── Backend availability ───────────────────────────────────────────────────────
try:
    from deepface import DeepFace
    BACKEND = 'deepface'
    logger.info("face_utils: deepface backend faol.")
except ImportError:
    DeepFace = None
    try:
        import cv2
        import numpy as np
        BACKEND = 'opencv'
        logger.info("face_utils: opencv backend faol.")
    except ImportError:
        cv2 = None
        np = None
        BACKEND = 'none'
        logger.warning(
            "face_utils: Hech qanday yuz taqqoslash kutubxonasi topilmadi! "
            "O'rnatish (kichik, ~50MB): pip install opencv-python-headless"
        )

FACE_RECOGNITION_AVAILABLE = BACKEND != 'none'

# deepface sozlamalari
DEEPFACE_MODEL    = "VGG-Face"
DEEPFACE_METRIC   = "cosine"
DEEPFACE_DETECTOR = "opencv"
DEEPFACE_THRESHOLD = 0.45  # cosine uchun: <0.45 => bir odam

# OpenCV sozlamalari
OPENCV_HIST_THRESHOLD = 0.60  # histogram correlation: >0.60 => bir odam


# ── Public helpers ─────────────────────────────────────────────────────────────

def decode_base64_image(face_b64: str) -> bytes:
    """data:image/jpeg;base64,... yoki toza base64 string => bytes."""
    if 'base64,' in face_b64:
        _, encoded = face_b64.split('base64,', 1)
    else:
        encoded = face_b64
    return base64.b64decode(encoded)


def verify_face(
    incoming_image_bytes: bytes,
    stored_avatar_path: str,
) -> Tuple[bool, str, float]:
    """
    Kameradan kelgan rasm bilan bazadagi avatar rasmini taqqoslaydi.

    Returns:
        (is_match: bool, message: str, distance: float)
    """
    if BACKEND == 'none':
        return False, (
            "Yuz taqqoslash tizimi faol emas. "
            "O'rnatish: pip install opencv-python-headless"
        ), 1.0

    if not os.path.isfile(stored_avatar_path):
        return False, f"Avatar fayli topilmadi: {stored_avatar_path}", 1.0

    if BACKEND == 'deepface':
        return _verify_deepface(incoming_image_bytes, stored_avatar_path)
    else:
        return _verify_opencv(incoming_image_bytes, stored_avatar_path)


# ── deepface backend ──────────────────────────────────────────────────────────

def _verify_deepface(
    incoming_bytes: bytes,
    avatar_path: str,
) -> Tuple[bool, str, float]:
    tmp_file = None
    try:
        with tempfile.NamedTemporaryFile(
            suffix='.jpg', delete=False, prefix='faceid_'
        ) as tmp:
            tmp.write(incoming_bytes)
            tmp_file = tmp.name

        result = DeepFace.verify(
            img1_path=tmp_file,
            img2_path=avatar_path,
            model_name=DEEPFACE_MODEL,
            distance_metric=DEEPFACE_METRIC,
            detector_backend=DEEPFACE_DETECTOR,
            enforce_detection=True,
            align=True,
        )
        distance  = float(result.get('distance', 1.0))
        verified  = bool(result.get('verified', False))
        threshold = float(result.get('threshold', DEEPFACE_THRESHOLD))
        is_match  = verified and distance < max(threshold, DEEPFACE_THRESHOLD)

        if is_match:
            pct = round((1.0 - distance / max(threshold, 0.001)) * 100, 1)
            pct = min(max(pct, 0.0), 100.0)
            return True, (
                f"Yuz tasdiqlandi ({pct:.1f}% aniqlik, d={distance:.4f})."
            ), distance
        return False, (
            f"Yuz mos kelmadi! "
            f"Boshqa odam yoki yuz berkitilgan. "
            f"(d={distance:.4f}, limit={threshold:.4f})"
        ), distance

    except ValueError as e:
        msg = str(e).lower()
        if any(w in msg for w in ['face', 'detect', 'found', 'cannot']):
            return False, (
                "Rasmda yuz aniqlanmadi! "
                "Yuzingizni to'g'ri kameraga qarating."
            ), 1.0
        return False, f"deepface xatosi: {e}", 1.0
    except Exception as e:
        logger.error("_verify_deepface xato: %s", e, exc_info=True)
        return False, f"Yuz taqqoslashda server xatosi: {e}", 1.0
    finally:
        if tmp_file and os.path.isfile(tmp_file):
            try:
                os.unlink(tmp_file)
            except Exception:
                pass


# ── OpenCV backend (Haar Cascade + Histogram taqqoslash) ─────────────────────

_CASCADE_CLASSIFIER = None

def _get_cascade_classifier():
    """Haar Cascade modelini xavfsiz yuklaydi va xotirada keshlaydi."""
    global _CASCADE_CLASSIFIER
    if _CASCADE_CLASSIFIER is not None:
        return _CASCADE_CLASSIFIER, None

    if cv2 is None:
        return None, "OpenCV (cv2) moduli tizimda o'rnatilmagan."

    # cv2 da CascadeClassifier mavjudligini tekshirish
    if not hasattr(cv2, 'CascadeClassifier'):
        return None, (
            "OpenCV da 'CascadeClassifier' topilmadi. "
            "Tavsiya: OpenCV 4.x barqaror versiyasidan foydalaning (pip install 'opencv-python<5')."
        )

    # haarcascades XML faylini topish
    cascade_dir = getattr(getattr(cv2, 'data', None), 'haarcascades', '')
    cascade_path = os.path.join(cascade_dir, 'haarcascade_frontalface_default.xml') if cascade_dir else ''

    if not cascade_path or not os.path.isfile(cascade_path):
        return None, f"Haar cascade XML fayli topilmadi: {cascade_path}"

    try:
        classifier = cv2.CascadeClassifier(cascade_path)
        if classifier.empty():
            return None, "Haar Cascade model faylini yuklab bo'lmadi (bo'sh klassifikator)."
        _CASCADE_CLASSIFIER = classifier
        return _CASCADE_CLASSIFIER, None
    except Exception as e:
        logger.error("CascadeClassifier yuklashda xatolik: %s", e)
        return None, f"CascadeClassifier xatosi: {e}"


def _load_gray_face(image_bytes: bytes):
    """
    image_bytes => OpenCV grayscale yuz tasviri.
    Haar Cascade bilan yuz aniqlanadi va crop qilinadi.
    Returns: (face_roi, None) yoki (None, error_msg)
    """
    if cv2 is None or np is None:
        return None, "OpenCV (cv2) moduli o'rnatilmagan."

    classifier, err = _get_cascade_classifier()
    if classifier is None:
        return None, err or "Yuz aniqlash modeli faol emas."

    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img   = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None, "Rasm o'qib bo'lmadi (noto'g'ri format)."

        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        scale_flag = getattr(cv2, 'CASCADE_SCALE_IMAGE', 2)
        faces = classifier.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=5,
            minSize=(60, 60),
            flags=scale_flag,
        )
        if len(faces) == 0:
            return None, "Rasmda yuz aniqlanmadi. Yuzingizni to'g'ri kameraga qarating."

        # Eng katta yuzni olish
        x, y, w, h = max(faces, key=lambda r: r[2] * r[3])
        face_roi   = cv2.resize(gray[y:y+h, x:x+w], (200, 200))
        return face_roi, None
    except Exception as e:
        logger.error("_load_gray_face xatosi: %s", e, exc_info=True)
        return None, f"Yuzni tahlil qilishda xatolik yuz berdi: {e}"


def _hist_similarity(face1, face2) -> float:
    """Ikki grayscale yuz ROI o'rtasida histogram korrelyatsiyasini hisoblaydi."""
    hist1 = cv2.calcHist([face1], [0], None, [256], [0, 256])
    hist2 = cv2.calcHist([face2], [0], None, [256], [0, 256])
    cv2.normalize(hist1, hist1)
    cv2.normalize(hist2, hist2)
    # CORREL: 1.0 = bir xil, 0.0 = mutlaqo boshqa
    return float(cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL))


def _verify_opencv(
    incoming_bytes: bytes,
    avatar_path: str,
) -> Tuple[bool, str, float]:
    try:
        # Kelgan rasmdan yuz ajratish
        face_new, err = _load_gray_face(incoming_bytes)
        if face_new is None:
            return False, f"Kelgan rasmda muammo: {err}", 1.0

        # Avatar rasmdan yuz ajratish
        with open(avatar_path, 'rb') as f:
            avatar_bytes = f.read()
        face_ref, err = _load_gray_face(avatar_bytes)
        if face_ref is None:
            return False, f"Avatar rasmida muammo: {err}", 1.0

        similarity = _hist_similarity(face_new, face_ref)
        # distance = 1 - similarity (0.0 => bir xil, 1.0 => mutlaqo boshqa)
        distance   = round(1.0 - similarity, 4)

        is_match = similarity >= OPENCV_HIST_THRESHOLD
        if is_match:
            pct = round(similarity * 100, 1)
            return True, (
                f"Yuz tasdiqlandi ({pct:.1f}% o'xshashlik)."
            ), distance
        return False, (
            f"Yuz mos kelmadi! "
            f"Boshqa odam yoki yuz berkitilgan. "
            f"(O'xshashlik: {similarity:.2%}, Talab: {OPENCV_HIST_THRESHOLD:.0%})"
        ), distance
    except Exception as e:
        logger.error("_verify_opencv xatosi: %s", e, exc_info=True)
        return False, f"OpenCV orqali tekshirishda xatolik: {e}", 1.0
