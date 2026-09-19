import { ImageResponse } from "next/og";

/*  The home-screen icon: Threshold, white tile on black. The renderer has no SVG masks,
 *  so the cut-out is drawn the other way round — black strokes over a white tile give
 *  the same picture on a black ground. Same geometry as app/icon.svg, scaled ×4. */

export const size = { width: 180, height: 180 };
export const contentType = "image/png";

export default function AppleIcon() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "#08080a",
        }}
      >
        <svg width="128" height="128" viewBox="0 0 32 32">
          <rect width="32" height="32" rx="9" fill="#fafafa" />
          <path d="M8.5 9.5H22.5L9.5 22.5H19" fill="none" stroke="#08080a" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round" />
          <circle cx="23.2" cy="22.5" r="4" fill="#08080a" />
          <circle cx="23.2" cy="22.5" r="1.7" fill="#fafafa" />
        </svg>
      </div>
    ),
    size,
  );
}
