/** Tailwind v3 (compilé hors ligne : scripts/build_css.sh ou .ps1 → app/web/static/app.css). */
module.exports = {
  content: ["./app/web/templates/**/*.html"],
  darkMode: "media",
  theme: {
    extend: {
      fontFamily: { sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"] },
    },
  },
};
