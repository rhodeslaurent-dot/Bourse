# Recompile app/web/static/app.css après modification des gabarits (Windows).
# Binaire autonome : https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-windows-x64.exe
# à placer dans scripts\tailwindcss.exe (ignoré par git), sinon npx est utilisé.
Set-Location (Join-Path $PSScriptRoot "..")
if (Test-Path "scripts\tailwindcss.exe") { & scripts\tailwindcss.exe -c tailwind.config.js -i app\web\static\src.css -o app\web\static\app.css --minify }
else { npx tailwindcss@3.4.17 -c tailwind.config.js -i app\web\static\src.css -o app\web\static\app.css --minify }
