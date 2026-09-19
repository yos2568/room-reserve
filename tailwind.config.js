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
        // FAA identity (D-33): the crimson of the faculty logo and the
        // entrance lattice, with a plaster-white paper tone.
        brand: {
          50: "#fbf2f2",
          100: "#f5dfe0",
          200: "#e8c0c3",
          400: "#c9696f",
          500: "#b23a42",
          600: "#a02b33",
          700: "#832229",
        },
        paper: "#f6f5f1",
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
