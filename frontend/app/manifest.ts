import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "Zenith — Personal Manager",
    short_name: "Zenith",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#171916",
    theme_color: "#171916",
    description: "A calm, local-first personal manager.",
    icons: [{ src: "/icon.svg", sizes: "any", type: "image/svg+xml", purpose: "maskable" }],
  };
}
