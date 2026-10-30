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
          deep: "#1d4a60", // hero and header bands
        },
        // The warm second colour: the money / "start here" colour. Used for plan prices, trial and upgrade buttons and the sign-up call to action.
        brass: {
          DEFAULT: "#9a7220", // button fill (white text reads at 4.8:1)
          dark: "#7d5b17",
          light: "#b5892f", // borders, flags, large figures
          soft: "#f7eed8",
        },
        wa: "#1f8f55", // the WhatsApp action
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
