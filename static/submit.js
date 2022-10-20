(function() {
    var endpoint = document.currentScript.getAttribute("data-preview-url");

    function setupPreview(input, output) {

        var pending = null;
        async function genPreview() {
            var uri = document.getElementById(input).value;
            if (!uri) {
                return;
            }

            var check =  + '?url=' + encodeURIComponent(uri);

            if (pending !== null) {
                // We have a pending delay; cancel it
                pending(false);
            }

            var preview = document.getElementById(output);

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
                data = await response.text();

                if (response.status == 200) {
                    preview.innerHTML = data;
                } else if (data) {
                    preview.innerHTML = `<p class="error">Error: ${data}</p>

                    <p>Not all audio sources are supported, but we'll make an effort to
                    add your music if it can be embedded. If you have more information about
                    an audio source that you'd like to support, please
                    <a href="https://github.com/fluffy-critter/novembeat.com/issues">open
                    an issue on GitHub</a> and we'll try to add it.</p>
                    `;
                } else {
                    preview.innerHTML = "Couldn't get preview";
                }

                preview.className = "resolved";
            } catch (error) {
                console.log(error);
                preview.className = "error";
                preview.innerHTML = "Error getting preview: " + error;
            }
        }

        window.addEventListener('DOMContentLoaded', () => {
            genPreview();
            document.getElementById(input).addEventListener('input', genPreview);
        });
    }

    setupPreview('entry-url', 'entry-preview');
    setupPreview('alternate-url', 'alternate-preview');

})();

