/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // warm neutral ground + single steel-blue accent, from the visual plan
        ground: "#f4f1ec",
        ink: "#23201b",
        card: "#ffffff",
        line: "#e6e0d6",
        muted: "#7c766a",
        accent: {
          DEFAULT: "#2f6f89",
          dark: "#245771",
          soft: "#eef4f7",
        },
        warn: "#b5892f",
        ok: "#4f8a5b",
        danger: "#c0392b",
      },
      fontFamily: {
        serif: ['"Fraunces"', "Georgia", "serif"],
        sans: ['"IBM Plex Sans"', "system-ui", "sans-serif"],
      },
      borderRadius: { xl: "10px" },
    },
  },
  plugins: [],
};
