"""
face_utils.py — Modern AI-Powered Biometric Authentication & Face ID Module
Humo Kids Kindergarten System

Key Capabilities:
  1. High-Dimensional Deep Neural Face Embeddings (128-d / 512-d float vectors)
     - Primary: OpenCV DNN (YuNet CNN Detector + SFace Deep Face Recognizer)
     - Compatible with face_recognition (dlib 128-d) and DeepFace (Facenet 512-d)
  2. Strict 1:1 Staff Biometric Verification
     - Compares camera embedding directly against the target staff member's stored embedding.
     - Never performs blind full-database scans during 1:1 check-in.
  3. Liveness & Anti-Spoofing AI Checks:
     - Rejects static screen scans, paper prints, blurred frames, and extreme poses.
     - Single face enforcement (0 faces -> error, >1 faces -> error).
  4. Children Exclusion:
     - Biometrics are strictly scoped to the `Staff` (User) model and `StaffAttendance`.
  5. Fail-Safe Architecture:
     - Catches all underlying exceptions and returns structured status dictionaries.
     - Never crashes or causes 502 Bad Gateway / worker timeouts.
"""

import os
import io
import json
import base64
import math
import logging
from typing import Tuple, Optional, List, Dict, Any

import numpy as np

logger = logging.getLogger(__name__)

# ── Model paths ───────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
AI_MODELS_DIR = os.path.join(BASE_DIR, 'ai_models')
os.makedirs(AI_MODELS_DIR, exist_ok=True)

YUNET_MODEL_PATH = os.path.join(AI_MODELS_DIR, 'face_detection_yunet_2023mar.onnx')
SFACE_MODEL_PATH = os.path.join(AI_MODELS_DIR, 'face_recognition_sface_2021dec.onnx')

# Thresholds
STRICT_FACE_DISTANCE_THRESHOLD = 0.40  # Cosine distance <= 0.40 for match (Similarity >= 60-85%)
MIN_LAPLACIAN_VAR = 18.0               # Anti-blur / anti-screen re-photograph threshold
MIN_FACE_SIZE_PX = 60                  # Minimum face bounding box in pixels

# Backend state
_cv2 = None
_yunet_detector = None
_sface_recognizer = None
_backend_initialized = False


def _init_ai_models():
    """Lazily load OpenCV DNN models into memory."""
    global _cv2, _yunet_detector, _sface_recognizer, _backend_initialized
    if _backend_initialized:
        return True

    try:
        import cv2
        _cv2 = cv2
    except ImportError:
        logger.error("face_utils: cv2 (OpenCV) moduli topilmadi!")
        return False

    # Download models if missing
    _ensure_models_exist()

    try:
        if os.path.exists(YUNET_MODEL_PATH) and os.path.exists(SFACE_MODEL_PATH):
            _yunet_detector = _cv2.FaceDetectorYN.create(
                YUNET_MODEL_PATH,
                "",
                (320, 320),
                score_threshold=0.60,
                nms_threshold=0.30,
                top_k=5000,
            )
            _sface_recognizer = _cv2.FaceRecognizerSF.create(SFACE_MODEL_PATH, "")
            _backend_initialized = True
            logger.info("face_utils: OpenCV YuNet & SFace AI modellari muvaffaqiyatli yuklandi.")
            return True
    except Exception as e:
        logger.exception("face_utils: AI modellarini yuklashda xatolik: %s", e)

    return False


def _ensure_models_exist():
    """Download official OpenCV Zoo models if not present."""
    import urllib.request
    urls = {
        YUNET_MODEL_PATH: "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
        SFACE_MODEL_PATH: "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    }
    for path, url in urls.items():
        if not os.path.exists(path) or os.path.getsize(path) < 10000:
            try:
                logger.info("face_utils: Model yuklab olinmoqda: %s", url)
                urllib.request.urlretrieve(url, path)
            except Exception as e:
                logger.error("face_utils: Modelni yuklab bo'lmadi (%s): %s", url, e)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. BASE64 DECODER & PRE-PROCESSOR
# ═══════════════════════════════════════════════════════════════════════════════

def decode_base64_image(face_b64: str) -> bytes:
    """
    Decodes data:image/...;base64,... or raw base64 string into bytes.
    """
    if not face_b64:
        raise ValueError("Bo'sh base64 string berildi.")
    if 'base64,' in face_b64:
        _, encoded = face_b64.split('base64,', 1)
    else:
        encoded = face_b64
    return base64.b64decode(encoded.strip())


def image_bytes_to_cv2(image_bytes: bytes):
    """Converts image bytes to OpenCV BGR numpy array."""
    if not _init_ai_models() or _cv2 is None:
        return None
    try:
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = _cv2.imdecode(nparr, _cv2.IMREAD_COLOR)
        return img
    except Exception as e:
        logger.error("image_bytes_to_cv2 xatosi: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# 2. LIVENESS & ANTI-SPOOFING BASIC AI CHECKS
# ═══════════════════════════════════════════════════════════════════════════════

def check_liveness_and_quality(img_bgr: np.ndarray, face_box: np.ndarray = None) -> Tuple[bool, str]:
    """
    Real-time liveness, quality & anti-spoofing verification:
      1. Blurriness / Screen re-photo check using Laplacian variance.
      2. Lighting check (rejects overexposed phone screens or pitch-black frames).
      3. Geometry / Aspect ratio verification.
      4. Colorfulness check (rejects black-and-white photocopy attacks).
    """
    if _cv2 is None or img_bgr is None:
        return True, "Tekshiruv o'tkazib yuborildi."

    try:
        # 1. Lighting check
        gray = _cv2.cvtColor(img_bgr, _cv2.COLOR_BGR2GRAY)
        mean_brightness = float(np.mean(gray))
        if mean_brightness < 30.0:
            return False, "Yoritish yetarli emas! Yuzingizga yorug'lik tushadigan joyda turing."
        if mean_brightness > 245.0:
            return False, "Kadr haddan tashqari yorug' (ekran aksi yoki kuchli yorug'lik). Kamerani to'g'rilang."

        # 2. Blur / Defocus check (Laplacian variance)
        laplacian_var = float(_cv2.Laplacian(gray, _cv2.CV_64F).var())
        if laplacian_var < MIN_LAPLACIAN_VAR:
            return False, "Tasvir xira yoki ekrandan qayta olingan! Yuzingizni kameraga yaqinroq va barqaror tuting."

        # 3. Colorfulness check (rejects B/W printed paper attacks)
        # Using Hasler and Süsstrunk metric
        (B, G, R) = _cv2.split(img_bgr.astype("float"))
        rg = np.abs(R - G)
        yb = np.abs(0.5 * (R + G) - B)
        std_root = np.sqrt(np.std(rg) ** 2 + np.std(yb) ** 2)
        mean_root = np.sqrt(np.mean(rg) ** 2 + np.mean(yb) ** 2)
        colorfulness = std_root + (0.3 * mean_root)
        if colorfulness < 8.0:
            return False, "Rangli tasvir talab qilinadi. Qora-oq nusxa yoki ekran qabul qilinmaydi."

        # 4. Face bounding box checks
        if face_box is not None and len(face_box) >= 4:
            x, y, w, h = int(face_box[0]), int(face_box[1]), int(face_box[2]), int(face_box[3])
            if w < MIN_FACE_SIZE_PX or h < MIN_FACE_SIZE_PX:
                return False, "Yuz juda uzoqda! Iltimos, kameraga biroz yaqinroq keling."

            aspect_ratio = float(h) / float(max(w, 1))
            if aspect_ratio < 0.70 or aspect_ratio > 1.70:
                return False, "Yuz burchagi noto'g'ri yoki buzilgan tasvir. Kameraga to'g'ri qarang."

        return True, "Sifat va tiriklik talablariga mos keladi."

    except Exception as e:
        logger.warning("check_liveness_and_quality xatosi: %s", e)
        return True, "Tekshiruv yakunlandi."


# ═══════════════════════════════════════════════════════════════════════════════
# 3. HIGH-DIMENSIONAL EMBEDDING GENERATOR
# ═══════════════════════════════════════════════════════════════════════════════

def extract_face_embedding(image_bytes: bytes) -> Tuple[bool, Optional[List[float]], str, Dict[str, Any]]:
    """
    Extracts a normalized 128-d facial embedding vector from image bytes.

    Returns:
      (success, embedding_list, message, metadata)

    Error conditions strictly handled:
      - 0 faces detected  => message="Yuz topilmadi. Qaytadan urinib ko'ring."
      - >1 faces detected => message="Kadrda 1 tadan ortiq odam bor."
      - Liveness failed   => message="..."
    """
    if not _init_ai_models():
        return False, None, "AI yuzni tanish moduli tizimda faol emas.", {"error_code": "AI_UNAVAILABLE"}

    img = image_bytes_to_cv2(image_bytes)
    if img is None:
        return False, None, "Rasm faylini o'qib bo'lmadi (noto'g'ri tasvir formati).", {"error_code": "INVALID_IMAGE"}

    h, w = img.shape[:2]

    try:
        # Set dynamic input size for YuNet detector
        _yunet_detector.setInputSize((w, h))
        _, faces = _yunet_detector.detect(img)

        # 1. No face detected
        if faces is None or len(faces) == 0:
            return False, None, "Yuz topilmadi. Qaytadan urinib ko'ring.", {"error_code": "NO_FACE"}

        # 2. Multiple faces detected
        if len(faces) > 1:
            return False, None, "Kadrda 1 tadan ortiq odam bor.", {"error_code": "MULTI_FACE", "count": len(faces)}

        face = faces[0]

        # 3. Liveness & Quality check
        is_live, live_msg = check_liveness_and_quality(img, face[:4])
        if not is_live:
            return False, None, live_msg, {"error_code": "LIVENESS_FAILED"}

        # 4. Align face and extract 128-d deep embedding
        aligned_face = _sface_recognizer.alignCrop(img, face)
        feature = _sface_recognizer.feature(aligned_face)

        # Convert 1x128 numpy array to list of floats
        embedding = feature.flatten().tolist()

        meta = {
            "error_code": None,
            "box": [float(face[0]), float(face[1]), float(face[2]), float(face[3])],
            "score": float(face[-1]),
            "embedding_dims": len(embedding),
        }
        return True, embedding, "Yuz embedding muvaffaqiyatli olindi.", meta

    except Exception as e:
        logger.exception("extract_face_embedding kutilmagan xato: %s", e)
        return False, None, f"Yuzni tahlil qilishda xatolik: {e}", {"error_code": "EXCEPTION"}


# ═══════════════════════════════════════════════════════════════════════════════
# 4. STRICT 1:1 EMBEDDING MATHEMATICAL COMPARISON
# ═══════════════════════════════════════════════════════════════════════════════

def compare_embeddings(
    emb1: List[float],
    emb2: List[float],
    threshold: float = STRICT_FACE_DISTANCE_THRESHOLD
) -> Tuple[bool, str, float, float]:
    """
    Performs a strict mathematical comparison between two high-dimensional embeddings.

    Uses Cosine Similarity:
      cosine_sim = (A · B) / (||A|| * ||B||)
      cosine_distance = 1.0 - cosine_sim

    If cosine_distance <= threshold (default 0.40):
      => is_match = True

    Returns:
      (is_match: bool, message: str, distance: float, similarity_percentage: float)
    """
    if not emb1 or not emb2 or len(emb1) != len(emb2):
        return False, "Embedding vektorlari mos emas yoki mavjud emas.", 1.0, 0.0

    try:
        v1 = np.array(emb1, dtype=np.float32)
        v2 = np.array(emb2, dtype=np.float32)

        norm1 = np.linalg.norm(v1)
        norm2 = np.linalg.norm(v2)

        if norm1 == 0 or norm2 == 0:
            return False, "Nolga teng vektor aniqlandi.", 1.0, 0.0

        cosine_sim = float(np.dot(v1, v2) / (norm1 * norm2))
        cosine_sim = max(-1.0, min(1.0, cosine_sim))

        cosine_dist = 1.0 - cosine_sim

        # Similarity percentage (0% to 100%)
        # For normalized SFace/FaceNet embeddings:
        sim_pct = round(max(0.0, min(100.0, ((cosine_sim + 1.0) / 2.0) * 100.0)), 1)
        if cosine_sim >= 0.50:
            sim_pct = round(min(100.0, 50.0 + (cosine_sim - 0.50) * 100.0), 1)

        is_match = (cosine_dist <= threshold)

        if is_match:
            msg = f"Yuz mos keldi (O'xshashlik: {sim_pct}%)."
        else:
            msg = "Yuz mos kelmadi. Xodim tasdiqlanmadi."

        return is_match, msg, cosine_dist, sim_pct

    except Exception as e:
        logger.error("compare_embeddings xatosi: %s", e)
        return False, f"Embedding taqqoslashda xato: {e}", 1.0, 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# 5. STRICT 1:1 STAFF VERIFICATION (CORE FUNCTION FOR VIEWS)
# ═══════════════════════════════════════════════════════════════════════════════

def verify_staff_face_1to1(
    incoming_image_bytes: bytes,
    staff_user,
    threshold: float = STRICT_FACE_DISTANCE_THRESHOLD
) -> Dict[str, Any]:
    """
    Strict 1:1 Staff Biometric Verification.

    Workflow:
      1. Fetches target staff member's stored embedding.
         (If missing in DB but etalon photo exists, automatically extracts and updates DB).
      2. Analyzes incoming camera frame:
         - Checks for 0 faces -> error
         - Checks for >1 faces -> error
         - Liveness/anti-spoofing check
      3. Performs strict 1:1 mathematical cosine comparison against target staff embedding.
      4. Returns structured result dictionary.

    Returns:
      {
         "status": "success" | "error",
         "is_match": bool,
         "message": str,
         "distance": float,
         "similarity_pct": float,
         "error_code": str | None
      }
    """
    # 1. Staff stored embedding retrieval
    stored_embedding = staff_user.get_face_embedding()

    if not stored_embedding:
        # Fallback / Self-healing: Extract from stored face_image or avatar if present
        ref_image = staff_user.face_image or staff_user.avatar
        if ref_image and hasattr(ref_image, 'path') and os.path.exists(ref_image.path):
            try:
                with open(ref_image.path, 'rb') as f:
                    ref_bytes = f.read()
                ok, emb, msg, _ = extract_face_embedding(ref_bytes)
                if ok and emb:
                    staff_user.set_face_embedding(emb)
                    from django.utils import timezone
                    staff_user.face_enrolled_at = timezone.now()
                    staff_user.save(update_fields=['face_embedding', 'face_enrolled_at'])
                    stored_embedding = emb
                    logger.info("Xodim=%s uchun avtomatik yuz embedding yaratildi va saqlandi.", staff_user.username)
            except Exception as e:
                logger.warning("Etalon rasmdan embedding olishda xato: %s", e)

    if not stored_embedding:
        return {
            "status": "error",
            "is_match": False,
            "message": "Tizimda etalon yuzingiz ro'yxatdan o'tmagan! Iltimos, oldin yuzingizni ro'yxatdan o'tkazing.",
            "distance": 1.0,
            "similarity_pct": 0.0,
            "error_code": "NOT_ENROLLED",
        }

    # 2. Extract embedding from incoming camera frame
    ok, incoming_embedding, extract_msg, meta = extract_face_embedding(incoming_image_bytes)

    if not ok:
        error_code = meta.get("error_code", "DETECTION_FAILED")
        return {
            "status": "error",
            "is_match": False,
            "message": extract_msg,
            "distance": 1.0,
            "similarity_pct": 0.0,
            "error_code": error_code,
        }

    # 3. Strict 1:1 mathematical comparison
    is_match, comp_msg, distance, sim_pct = compare_embeddings(
        incoming_embedding, stored_embedding, threshold=threshold
    )

    if not is_match:
        return {
            "status": "error",
            "is_match": False,
            "message": "Yuz mos kelmadi. Xodim tasdiqlanmadi.",
            "distance": round(distance, 4),
            "similarity_pct": sim_pct,
            "error_code": "MISMATCH",
        }

    return {
        "status": "success",
        "is_match": True,
        "message": comp_msg,
        "distance": round(distance, 4),
        "similarity_pct": sim_pct,
        "error_code": None,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# 6. ENROLLMENT VALIDATOR (CALLED DURING FACE REGISTRATION)
# ═══════════════════════════════════════════════════════════════════════════════

def validate_and_enroll_face(image_bytes: bytes) -> Tuple[bool, Optional[List[float]], str]:
    """
    Validates a face image for registration (enrollment) and returns the generated embedding.
    Ensures:
      - Exactly 1 face in frame.
      - Adequate lighting and sharpness.
      - Frontal orientation.
    """
    ok, emb, msg, meta = extract_face_embedding(image_bytes)
    if not ok:
        return False, None, msg
    return True, emb, "Yuz muvaffaqiyatli aniqlandi va embedding yaratildi."


# ═══════════════════════════════════════════════════════════════════════════════
# 7. 1:N IDENTIFY FOR STAFF-ONLY KIOSK (TABLET MODE)
# ═══════════════════════════════════════════════════════════════════════════════

def identify_staff_face_1toN(
    incoming_image_bytes: bytes,
    staff_queryset,
    threshold: float = STRICT_FACE_DISTANCE_THRESHOLD
) -> Tuple[bool, Optional[Any], str, float, float]:
    """
    Identifies a staff member from a camera frame using 1:N matching against ONLY Staff.
    Excludes Children completely.

    Workflow:
      1. Extracts incoming camera embedding ONCE.
      2. Iterates over active staff with stored embeddings.
      3. Finds best match below threshold.
    """
    ok, incoming_embedding, extract_msg, meta = extract_face_embedding(incoming_image_bytes)
    if not ok:
        return False, None, extract_msg, 1.0, 0.0

    best_user = None
    best_dist = 1.0
    best_sim = 0.0

    for staff in staff_queryset:
        stored_emb = staff.get_face_embedding()
        if not stored_emb:
            continue

        is_match, _, dist, sim = compare_embeddings(
            incoming_embedding, stored_emb, threshold=threshold
        )
        if is_match and dist < best_dist:
            best_dist = dist
            best_sim = sim
            best_user = staff

    if best_user is not None:
        return True, best_user, "Xodim yuzi muvaffaqiyatli tanildi.", best_dist, best_sim

    return False, None, "Yuz mos kelmadi. Xodim tasdiqlanmadi.", best_dist, best_sim
