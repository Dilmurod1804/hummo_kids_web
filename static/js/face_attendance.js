/**
 * HUMO KIDS — Staff Face ID & GPS Geofencing Attendance Module
 */

let videoStream = null;
let currentCoords = null;

// Initialize camera and geolocation
async function initAttendanceScanner() {
    const videoElem = document.getElementById('cameraVideo');
    const statusElem = document.getElementById('scannerStatus');
    const gpsInfoElem = document.getElementById('gpsInfoText');
    const distanceElem = document.getElementById('distancePreview');

    // 1. Get Geolocation
    if (navigator.geolocation) {
        statusElem.innerHTML = "<span class='text-amber-400'>📡 GPS koordinatalari aniqlanmoqda...</span>";
        navigator.geolocation.getCurrentPosition(
            (position) => {
                currentCoords = {
                    lat: position.coords.latitude,
                    lng: position.coords.longitude,
                    accuracy: position.coords.accuracy
                };
                if (gpsInfoElem) {
                    gpsInfoElem.innerText = `Sizning koordinatangiz: ${currentCoords.lat.toFixed(6)}, ${currentCoords.lng.toFixed(6)} (Aniqlik: ±${Math.round(currentCoords.accuracy)}m)`;
                }
                
                // Calculate distance preview if kindergarten coords are provided in data attributes
                const kLat = parseFloat(document.getElementById('attendanceScannerContainer').dataset.kLat || 41.311081);
                const kLng = parseFloat(document.getElementById('attendanceScannerContainer').dataset.kLng || 69.240562);
                const radius = parseFloat(document.getElementById('attendanceScannerContainer').dataset.kRadius || 50);

                const dist = calculateHaversine(currentCoords.lat, currentCoords.lng, kLat, kLng);
                if (distanceElem) {
                    distanceElem.innerText = `${dist.toFixed(1)} metr (Ruxsat: ${radius}m)`;
                    const dot = document.getElementById('gpsRadarDot');
                    if (dot) {
                        if (dist <= radius) {
                            dot.className = 'gps-pulse-dot in-range';
                            statusElem.innerHTML = "<span style='color: #34d399;'>✓ Siz bog'cha hududasiz (50m ichida). Yuzingizni to'g'rilang.</span>";
                        } else {
                            dot.className = 'gps-pulse-dot out-range';
                            statusElem.innerHTML = `<span style='color: #fb7185;'>⚠️ Bog'cha hududidan tashqaridasiz (${dist.toFixed(1)}m > ${radius}m)</span>`;
                        }
                    }
                }
            },
            (error) => {
                console.warn("Geolocation warning:", error);
                statusElem.innerHTML = "<span style='color: #fbbf24;'>⚠️ GPS aniqlanmadi (Demo koordinata o'rnatildi).</span>";
                currentCoords = { lat: 41.311090, lng: 69.240570, accuracy: 10 };
            },
            { enableHighAccuracy: true, timeout: 8000, maximumAge: 0 }
        );
    }

    // 2. Start Camera
    try {
        if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
            videoStream = await navigator.mediaDevices.getUserMedia({
                video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }
            });
            if (videoElem) {
                videoElem.srcObject = videoStream;
                videoElem.play();
            }
        }
    } catch (err) {
        console.warn("Camera stream error:", err);
        if (statusElem) {
            statusElem.innerHTML += "<br><span style='color: #fbbf24;'>Kamera ochilmadi yoki ruxsat berilmadi. Demo rejimda davom etishingiz mumkin.</span>";
        }
    }
}

// Haversine formula client-side preview
function calculateHaversine(lat1, lon1, lat2, lon2) {
    const R = 6371000; // meters
    const toRad = (x) => x * Math.PI / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
}

// Capture Face and Submit Attendance
async function captureAndSubmitAttendance(actionType = 'check_in') {
    const statusElem = document.getElementById('scannerStatus');
    const submitBtn = document.getElementById('btnSubmitCheckIn');
    
    if (submitBtn) submitBtn.disabled = true;
    statusElem.innerHTML = "<span style='color: #c084fc;'>⏳ Yuz skanerlanmoqda va GPS tekshirilmoqda...</span>";

    // 1. Capture snapshot from video canvas
    let base64Image = '';
    const videoElem = document.getElementById('cameraVideo');
    if (videoElem && videoElem.videoWidth > 0) {
        const canvas = document.createElement('canvas');
        canvas.width = videoElem.videoWidth;
        canvas.height = videoElem.videoHeight;
        const ctx = canvas.getContext('2d');
        ctx.drawImage(videoElem, 0, 0, canvas.width, canvas.height);
        base64Image = canvas.toDataURL('image/jpeg', 0.85);
    }

    // Default fallback coordinates if GPS was denied
    const coords = currentCoords || { lat: 41.311081, lng: 69.240562 };

    const payload = {
        latitude: coords.lat,
        longitude: coords.lng,
        action: actionType,
        face_image: base64Image
    };

    try {
        const response = await fetch('/attendance/staff/check-in-api/', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify(payload)
        });

        const result = await response.json();

        if (result.success) {
            statusElem.innerHTML = `<span style='color: #34d399; font-weight: bold;'>🎉 ${result.message} (Vaqt: ${result.time})</span>`;
            showToast(result.message, 'success');
            setTimeout(() => {
                window.location.reload();
            }, 1800);
        } else {
            statusElem.innerHTML = `<span style='color: #fb7185; font-weight: bold;'>❌ ${result.message}</span>`;
            showToast(result.message, 'error');
            if (submitBtn) submitBtn.disabled = false;
        }
    } catch (e) {
        statusElem.innerHTML = `<span style='color: #fb7185;'>Server bilan bog'lanishda xatolik: ${e.message}</span>`;
        if (submitBtn) submitBtn.disabled = false;
    }
}

// Stop camera on modal close/page leave
function stopCameraStream() {
    if (videoStream) {
        videoStream.getTracks().forEach(track => track.stop());
        videoStream = null;
    }
}

// Toast notification helper
function showToast(message, type = 'info') {
    let container = document.getElementById('toast-container');
    if (!container) {
        container = document.createElement('div');
        container.id = 'toast-container';
        container.style.cssText = 'position: fixed; bottom: 24px; right: 24px; z-index: 9999; display: flex; flex-direction: column; gap: 10px;';
        document.body.appendChild(container);
    }

    const toast = document.createElement('div');
    const bgColor = type === 'success' ? 'rgba(16, 185, 129, 0.95)' : (type === 'error' ? 'rgba(244, 63, 94, 0.95)' : 'rgba(99, 102, 241, 0.95)');
    
    toast.style.cssText = `background: ${bgColor}; backdrop-filter: blur(10px); color: white; padding: 14px 22px; border-radius: 14px; box-shadow: 0 10px 30px rgba(0,0,0,0.4); font-size: 14px; font-weight: 600; display: flex; align-items: center; gap: 10px; animation: slideIn 0.3s ease;`;
    toast.innerHTML = message;

    container.appendChild(toast);
    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateY(10px)';
        toast.style.transition = 'all 0.3s ease';
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}
