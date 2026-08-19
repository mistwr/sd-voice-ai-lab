import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#07070b",
        panel: "#101018",
        accent: "#a0ffb8",
        border: "#1f1f2b",
      },
      fontFamily: {
        mono: ["Space Mono", "monospace"],
        display: ["Syne", "sans-serif"],
      },
    },
  },
  plugins: [],
} satisfies Config;
