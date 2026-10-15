const ORBIT_RADII = Array.from({ length: 26 }, (_, index) => 280 + index * 44);
const ORBIT_LABEL = "THE CONTENT ARCHITECTURE · ";

export function ReferenceFooterOrbit() {
  return (
    <svg
      className="reference-footer-orbit"
      viewBox="0 0 1904 881"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden="true"
    >
      <defs>
        {ORBIT_RADII.map((radius, index) => (
          <path
            id={`reference-footer-orbit-${index}`}
            key={radius}
            d={`M 952 440.5 m 0 -${radius} a ${radius} ${radius} 0 1 1 0 ${radius * 2} a ${radius} ${radius} 0 1 1 0 -${radius * 2}`}
          />
        ))}
      </defs>
      {ORBIT_RADII.map((radius, index) => (
        <text key={radius} className="reference-footer-orbit-label">
          <textPath
            href={`#reference-footer-orbit-${index}`}
            startOffset={index % 2 === 0 ? "0%" : "50%"}
          >
            {ORBIT_LABEL.repeat(Math.ceil((Math.PI * radius * 2) / 360))}
          </textPath>
        </text>
      ))}
    </svg>
  );
}
