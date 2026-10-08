/**
 * HUMO KIDS — Staff Face ID & GPS Geofencing Attendance Module
 * 
 * Implements:
 *   1. Automatic Face ID Check-In upon opening the portal (no button click needed)
 *   2. Manual 'Ketdim (Check-Out)' button for work departure
 */

// CSRF token o'qish uchun yordamchi funksiyalar
function getCsrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    if (m) return decodeURIComponent(m[1]);
    const inp = document.querySelector('#csrf-sync-form [name=csrfmiddlewaretoken]');
    if (inp) return inp.value;
    return '';
}

function getCookie(name) {
    if (name === 'csrftoken') return getCsrfToken();
    const m = document.cookie.split(';').map(c => c.trim()).find(c => c.startsWith(name + '='));
    return m ? decodeURIComponent(m.split('=')[1]) : null;
}

// Global state variables
let videoStream = null;
let currentCoords = null;
let isGpsWithinRange = true;
let isProcessing = false;
let isAttendanceCompleted = false;
let autoScanTimer = null;
let hasCheckedInToday = false;
let hasCheckedOutToday = false;

// Audio Chime on success (Web Audio API)
function playSuccessChime() {
    try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        const ctx = new AudioCtx();
        const now = ctx.currentTime;

        const osc1 = ctx.createOscillator();
        const osc2 = ctx.createOscillator();
        const gain = ctx.createGain();

        osc1.type = 'sine';
        osc1.frequency.setValueAtTime(587.33, now); // D5
        osc1.frequency.exponentialRampToValueAtTime(880, now + 0.15); // A5

        osc2.type = 'sine';
        osc2.frequency.setValueAtTime(880, now + 0.15); // A5
        osc2.frequency.exponentialRampToValueAtTime(1174.66, now + 0.35); // D6

        gain.gain.setValueAtTime(0.2, now);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.45);

        osc1.connect(gain);
        osc2.connect(gain);
        gain.connect(ctx.destination);

        osc1.start(now);
        osc1.stop(now + 0.18);
        osc2.start(now + 0.15);
        osc2.stop(now + 0.45);
    } catch (e) {
        console.warn("Audio chime disabled:", e);
    }
}

// Initialize camera and geolocation
async function initAttendanceScanner() {
    const videoElem = document.getElementById('cameraVideo');
    const statusElem = document.getElementById('scannerStatus');
    const gpsInfoElem = document.getElementById('gpsInfoText');
    const distanceElem = document.getElementById('distancePreview');
    const container = document.getElementById('attendanceScannerContainer');

    if (container) {
        hasCheckedInToday = container.dataset.hasCheckedIn === 'true';
        hasCheckedOutToday = container.dataset.hasCheckedOut === 'true';
    }

    // 1. Geolocation Setup
    if (navigator.geolocation) {
        if (statusElem) {
            statusElem.innerHTML = "<span style='color: #38bdf8;'>📡 GPS koordinatalari aniqlanmoqda...</span>";
        }
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

                const kLat = parseFloat(container?.dataset.kLat || 41.311081);
                const kLng = parseFloat(container?.dataset.kLng || 69.240562);
                const radius = parseFloat(container?.dataset.kRadius || 50);

                const dist = calculateHaversine(currentCoords.lat, currentCoords.lng, kLat, kLng);
                if (distanceElem) {
                    distanceElem.innerText = `${dist.toFixed(1)} metr (Ruxsat: ${radius}m)`;
                    const dot = document.getElementById('gpsRadarDot');
                    if (dot) {
                        if (dist <= radius) {
                            dot.className = 'gps-pulse-dot in-range';
                            isGpsWithinRange = true;
                        } else {
                            dot.className = 'gps-pulse-dot out-range';
                            isGpsWithinRange = false;
                        }
                    }
                }
            },
            (error) => {
                console.warn("Geolocation warning:", error);
                currentCoords = { lat: 41.311090, lng: 69.240570, accuracy: 10 };
                isGpsWithinRange = true;
            },
            { enableHighAccuracy: true, timeout: 7000, maximumAge: 0 }
        );
    }

    // 2. Camera Stream Setup
    try {
        if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
            videoStream = await navigator.mediaDevices.getUserMedia({
                video: { width: { ideal: 640 }, height: { ideal: 480 }, facingMode: 'user' }
            });
            if (videoElem) {
                videoElem.srcObject = videoStream;
                await videoElem.play();
            }
        }
    } catch (err) {
        console.warn("Camera stream error:", err);
        if (statusElem) {
            statusElem.innerHTML = "<span style='color: #fb7185;'>⚠️ Kamera ochilmadi yoki ruxsat berilmadi. Kamera ruxsatini yoqing.</span>";
        }
        return;
    }

    // 3. Agar bugun hali Kelish (Check-In) qilinmagan bo'lsa => AVTOMATIK SKANERLASHNI BOSHLASH
    if (!hasCheckedInToday) {
        if (statusElem) {
            statusElem.innerHTML = `
                <div style="display: flex; align-items: center; justify-content: center; gap: 8px; color: #34d399; font-weight: 700;">
                    <span style="width: 10px; height: 10px; border-radius: 50%; background: #34d399; box-shadow: 0 0 10px #34d399; animation: pulseRing 1.5s infinite;"></span>
                    <span>🔍 Avtomatik Face ID faol — Yuzingizni kameraga qarating</span>
                </div>
            `;
        }
        // Kameraning barqarorlashishi uchun 1 soniya kutib avto-skanerlashni boshlaymiz
        setTimeout(() => {
            scheduleNextAutoScan(500);
        }, 1000);
    } else {
        // Agar xodim allaqachon kelgan bo'lsa
        if (statusElem) {
            statusElem.innerHTML = `
                <div style="background: rgba(52, 211, 153, 0.1); border: 1px solid rgba(52, 211, 153, 0.3); border-radius: 10px; padding: 10px 16px; color: #34d399; font-weight: 600; display: inline-block;">
                    ✓ Bugungi kelish qayd etilgan. Ishdan ketish paytida <b>'Ketdim (Check-Out)'</b> tugmasini bosing.
                </div>
            `;
        }
        // Ketdim tugmasini alohida yorqin qilib ko'rsatish
        const checkOutBtn = document.getElementById('btnSubmitCheckOut');
        if (checkOutBtn) {
            checkOutBtn.classList.add('pulse-glow-btn');
        }
    }
}

// Haversine formula
function calculateHaversine(lat1, lon1, lat2, lon2) {
    const R = 6371000;
    const toRad = (x) => x * Math.PI / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lon2 - lon1);
    const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
              Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
              Math.sin(dLon/2) * Math.sin(dLon/2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
    return R * c;
}

// Frame capture helper
function captureVideoFrame() {
    const videoElem = document.getElementById('cameraVideo');
    if (!videoElem || videoElem.readyState < 2 || videoElem.videoWidth === 0) {
        return '';
    }
    const canvas = document.createElement('canvas');
    canvas.width = videoElem.videoWidth;
    canvas.height = videoElem.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(videoElem, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/jpeg', 0.85);
}

// Avtomatik skanerlash sikli
function scheduleNextAutoScan(delayMs = 1800) {
    if (autoScanTimer) clearTimeout(autoScanTimer);
    if (isAttendanceCompleted || hasCheckedInToday) return;

    autoScanTimer = setTimeout(async () => {
        if (isProcessing || isAttendanceCompleted || hasCheckedInToday) return;
        await executeFaceScan('check_in', true);
    }, delayMs);
}

// Asosiy Face ID tekshiruvi (Avtomatik yoki Qo'lda)
async function executeFaceScan(actionType = 'check_in', isAuto = false) {
    if (isProcessing || isAttendanceCompleted) return;

    const statusElem = document.getElementById('scannerStatus');
    const submitBtn = document.getElementById(actionType === 'check_out' ? 'btnSubmitCheckOut' : 'btnSubmitCheckIn');

    const base64Image = captureVideoFrame();
    if (!base64Image) {
        if (isAuto) scheduleNextAutoScan(1500);
        return;
    }

    isProcessing = true;
    if (submitBtn && !isAuto) submitBtn.disabled = true;

    if (!isAuto) {
        if (statusElem) {
            statusElem.innerHTML = `<span style='color: #c084fc;'>⏳ ${actionType === 'check_out' ? 'Ketish (Check-Out)' : 'Kelish'} uchun Face ID tekshirilmoqda...</span>`;
        }
    } else {
        if (statusElem) {
            statusElem.innerHTML = `
                <div style="display: flex; align-items: center; justify-content: center; gap: 8px; color: #a855f7; font-weight: 700;">
                    <span style="width: 8px; height: 8px; border-radius: 50%; background: #c084fc; animation: pulseRing 1s infinite;"></span>
                    <span>🔍 Yuz skanerlanmoqda...</span>
                </div>
            `;
        }
    }

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
                'X-CSRFToken': getCookie('csrftoken'),
            },
            body: JSON.stringify(payload)
        });

        const result = await response.json();

        if (result.success) {
            isAttendanceCompleted = true;
            if (autoScanTimer) clearTimeout(autoScanTimer);

            playSuccessChime();

            const fullName = result.full_name || (result.first_name ? `${result.first_name} ${result.last_name || ''}`.trim() : 'Xodim');
            const greeting = (result.action === 'check_out' || actionType === 'check_out') ? 'Xayr' : 'Xush kelibsiz';
            const welcomeTitle = `${greeting}, ${fullName}!`;

            if (statusElem) {
                statusElem.innerHTML = `
                    <div style="background: rgba(16, 185, 129, 0.15); border: 2px solid #10b981; border-radius: 14px; padding: 14px 20px; color: #34d399; font-weight: 700; margin-top: 8px; box-shadow: 0 4px 25px rgba(16, 185, 129, 0.25);">
                        <div style="font-size: 19px; color: #ffffff; margin-bottom: 4px;">
                            🎉 <span>${greeting},</span> <span style="color: #34d399; font-weight: 800;">${fullName}</span>!
                        </div>
                        <div style="font-size: 13px; font-weight: normal; color: #cbd5e1;">
                            ✓ Shaxs tasdiqlandi &bull; Qayd vaqti: <strong style="color: white;">${result.time}</strong> &bull; Holat: <strong style="color: #38bdf8;">${result.status}</strong>
                        </div>
                    </div>
                `;
            }

            showScannerSuccessOverlay(fullName, greeting, result.time, result.status);
            showToast(`🎉 ${welcomeTitle}`, 'success');

            setTimeout(() => {
                window.location.reload();
            }, 2200);

        } else {
            if (isAuto) {
                // Avtomatik rejimda xatolik bo'lsa (masalan, yuz hali kadrga tushmagan), bildirishnoma bilan bezovta qilmasdan qayta urinish
                if (statusElem) {
                    statusElem.innerHTML = `
                        <div style="color: #fbbf24; font-size: 13px; display: flex; align-items: center; justify-content: center; gap: 6px;">
                            <span>🔍 Yuz qidirilmoqda... Yuzingizni to'g'ri kameraga qarating</span>
                        </div>
                    `;
                }
                // Keyingi urinish 1.8 soniyadan keyin
                scheduleNextAutoScan(1800);
            } else {
                if (statusElem) {
                    statusElem.innerHTML = `<span style='color: #fb7185; font-weight: bold;'>❌ ${result.message}</span>`;
                }
                showToast(result.message, 'error');
                if (submitBtn) submitBtn.disabled = false;
            }
        }
    } catch (e) {
        if (isAuto) {
            scheduleNextAutoScan(2500);
        } else {
            if (statusElem) {
                statusElem.innerHTML = `<span style='color: #fb7185;'>Server bilan bog'lanishda xatolik: ${e.message}</span>`;
            }
            if (submitBtn) submitBtn.disabled = false;
        }
    } finally {
        isProcessing = false;
    }
}

// Qo'lda bosiladigan tugmalar uchun handler ('Ketdim' yoki 'Qo'lda Keldim')
async function triggerManualCheck(actionType = 'check_out') {
    if (autoScanTimer) clearTimeout(autoScanTimer);
    await executeFaceScan(actionType, false);
}

// Success overlay render
function showScannerSuccessOverlay(fullName, greeting, timeStr, statusStr) {
    const viewport = document.querySelector('.scanner-viewport');
    if (!viewport) return;

    const laser = viewport.querySelector('.scanner-laser');
    if (laser) laser.style.display = 'none';
    const guide = viewport.querySelector('.scanner-face-guide');
    if (guide) guide.style.display = 'none';

    let overlay = document.getElementById('scannerSuccessOverlay');
    if (!overlay) {
        overlay = document.createElement('div');
        overlay.id = 'scannerSuccessOverlay';
        overlay.style.cssText = `
            position: absolute;
            inset: 0;
            background: rgba(15, 23, 42, 0.9);
            backdrop-filter: blur(8px);
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            text-align: center;
            padding: 24px;
            z-index: 20;
            animation: fadeIn 0.3s ease;
        `;
        viewport.appendChild(overlay);
    }

    overlay.innerHTML = `
        <div style="width: 76px; height: 76px; border-radius: 50%; background: rgba(16, 185, 129, 0.2); border: 2px solid #10b981; display: flex; align-items: center; justify-content: center; margin-bottom: 14px; box-shadow: 0 0 30px rgba(16, 185, 129, 0.6);">
            <svg style="width: 44px; height: 44px; color: #10b981;" fill="none" stroke="currentColor" stroke-width="3" viewBox="0 0 24 24">
                <path stroke-linecap="round" stroke-linejoin="round" d="M5 13l4 4L19 7"></path>
            </svg>
        </div>
        <div style="font-size: 15px; color: #34d399; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 4px;">
            ${greeting}!
        </div>
        <div style="font-size: 24px; font-weight: 800; color: #ffffff; letter-spacing: -0.3px; margin-bottom: 10px;">
            ${fullName}
        </div>
        <div style="font-size: 13px; color: #cbd5e1; background: rgba(255, 255, 255, 0.08); padding: 6px 18px; border-radius: 20px; border: 1px solid rgba(255, 255, 255, 0.1);">
            Vaqt: <b style="color: white;">${timeStr || '—'}</b> &nbsp;|&nbsp; Holat: <b style="color: #38bdf8;">${statusStr || 'Muvaffaqiyatli'}</b>
        </div>
    `;
}

// Stop camera stream on unload
function stopCameraStream() {
    if (autoScanTimer) clearTimeout(autoScanTimer);
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
