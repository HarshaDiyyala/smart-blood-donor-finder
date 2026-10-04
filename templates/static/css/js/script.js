// =====================================================
// REGISTRATION VALIDATION
// =====================================================

function validateRegistration() {

    const password =
        document.getElementById("password");

    const confirmPassword =
        document.getElementById("confirm_password");

    if (password && confirmPassword) {

        if (password.value !== confirmPassword.value) {

            alert("Passwords do not match.");

            confirmPassword.focus();

            return false;
        }
    }

    return true;
}


// =====================================================
// CAPTCHA REFRESH
// =====================================================

function refreshCaptcha() {

    const characters =
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

    let captcha = "";

    for (let i = 0; i < 6; i++) {

        captcha += characters.charAt(
            Math.floor(
                Math.random() * characters.length
            )
        );
    }

    const display =
        document.getElementById("captcha-display");

    if (display) {
        display.textContent = captcha;
    }

    /*
       Note:
       The server-side CAPTCHA is the actual
       verification mechanism in this prototype.
       For production, use a proper CAPTCHA service
       or a secure server-generated challenge.
    */
}


// =====================================================
// GET GPS LOCATION
// =====================================================

function getLocation() {

    const status =
        document.getElementById("location-status");

    if (!navigator.geolocation) {

        status.textContent =
            "Geolocation is not supported.";

        return;
    }

    status.textContent =
        "Getting location...";

    navigator.geolocation.getCurrentPosition(

        function(position) {

            document.getElementById("latitude").value =
                position.coords.latitude;

            document.getElementById("longitude").value =
                position.coords.longitude;

            status.textContent =
                " Location captured successfully.";

        },

        function(error) {

            status.textContent =
                " Location permission was not provided.";

        }
    );
}


// =====================================================
// 8-MINUTE RESPONSE TIMER
// =====================================================

function startResponseTimer() {

    const timerElement =
        document.getElementById("timer");

    const timerContainer =
        document.querySelector(".timer");

    if (!timerElement || !timerContainer) {
        return;
    }

    const createdString =
        timerContainer.dataset.created;

    if (!createdString) {
        return;
    }

    const createdTime =
        new Date(createdString);

    const deadline =
        createdTime.getTime()
        + (8 * 60 * 1000);


    function updateTimer() {

        const now =
            new Date().getTime();

        let remaining =
            Math.max(
                0,
                deadline - now
            );

        const totalSeconds =
            Math.floor(
                remaining / 1000
            );

        const minutes =
            Math.floor(
                totalSeconds / 60
            );

        const seconds =
            totalSeconds % 60;


        timerElement.textContent =
            String(minutes).padStart(2, "0")
            + ":"
            + String(seconds).padStart(2, "0");


        if (remaining <= 0) {

            timerElement.textContent =
                "00:00";

            timerElement.parentElement.classList.add(
                "expired"
            );
        }
    }


    updateTimer();

    setInterval(
        updateTimer,
        1000
    );
}


document.addEventListener(
    "DOMContentLoaded",
    function() {

        startResponseTimer();

    }
);