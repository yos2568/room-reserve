/** Tailwind CSS configuration — compiled locally, no CDN (V3 section 2). */

module.exports = {
  content: [
    "./core/templates/**/*.html",
    "./core/**/*.py",
    "./templates/**/*.html",
  ],
  theme: {
    extend: {
      colors: {
        // Faculty palette: restrained, high contrast, not colour-only signalling.
        brand: {
          50: "#eef4fb",
          100: "#d7e6f6",
          500: "#1f5f9e",
          600: "#194e83",
          700: "#143e69",
        },
      },
      minWidth: {
        touch: "44px",
      },
      minHeight: {
        touch: "44px",
      },
    },
  },
  plugins: [],
};
