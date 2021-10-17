(function() {
    var endpoint = document.currentScript.getAttribute("data-preview-url");

    var pending = null;
    async function genPreview() {
        var uri = document.getElementById('entry-url').value;
        if (!uri) {
            return;
        }

        var check =  + '?url=' + encodeURIComponent(uri);

        if (pending !== null) {
            // We have a pending delay; cancel it
            pending(false);
        }

        var preview = document.getElementById("entry-preview");

        delay = await new Promise(resolve => {
            pending = resolve;
            setTimeout(() => resolve(true), 250);
        });

        preview.className = "pending";
        preview.innerHTML = "Loading preview…";

        if (!delay) {
            return;
        }

        try {
            response = await fetch(endpoint + "?url=" + encodeURIComponent(uri));
            if (response.status !== 200) {
                preview.className = "error";
                preview.innerHTML = "Couldn't get preview: " + response.status;
                return;
            }

            data = await response.text();
            if (data) {
                preview.className = "resolved";
                preview.innerHTML = data;
            } else {
                preview.className = "error";
                preview.innerHTML = "Couldn't retrieve preview";
            }
        } catch (error) {
            console.log(error);
            preview.className = "error";
            preview.innerHTML = "Couldn't get preview: " + error;
        }
    }

    window.addEventListener('DOMContentLoaded', () => {
        genPreview();
        document.getElementById('entry-url').addEventListener('input', genPreview);
    });

})();

