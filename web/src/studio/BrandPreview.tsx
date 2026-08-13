import { useEffect, useState } from 'preact/hooks';
import { previewDesign, type DesignPreview } from '../lib/api';

/* The point of the Design panel: an adjusted colour has to be visible as
   adjusted. A silent correction is how a user ends up believing their brand is
   on the page when something near it is — so this shows what was asked for, what
   was used, and the measured reason for the difference. */
function Swatch({ hex, label }: { hex: string; label: string }) {
  return (
    <span class="swatch">
      <i style={{ background: hex }} />
      <span>{label}<br /><code>{hex}</code></span>
    </span>
  );
}

export function BrandPreview({ primary, accent, logoPath }: {
  primary: string | null;
  accent: string | null;
  logoPath: string | null;
}) {
  const [preview, setPreview] = useState<DesignPreview | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!primary && !logoPath) {
      setPreview(null);
      setError(null);
      return;
    }
    // Debounced: the colour fields fire per keystroke, and half a hex code is
    // a colour error the user has not finished making yet.
    const timer = setTimeout(() => {
      previewDesign({ primary, accent, logo_path: logoPath })
        .then((next) => { setPreview(next); setError(null); })
        .catch((err: Error) => { setError(err.message); setPreview(null); });
    }, 250);
    return () => clearTimeout(timer);
  }, [primary, accent, logoPath]);

  if (error) return <div id="brand-preview" class="brand-preview"><p class="warn">{error}</p></div>;
  if (preview === null) return <div id="brand-preview" class="brand-preview" />;

  const palette = preview.palettes.light;
  const logo = preview.logo;

  return (
    <div id="brand-preview" class="brand-preview">
      <div class="swatch-row">
        {palette.series.map((colour, index) => (
          <Swatch key={index} hex={colour} label={`series ${index + 1}`} />
        ))}
        <Swatch label="headings"
                hex={palette.tokens['heading_accent'] ?? palette.tokens['series_1'] ?? ''} />
      </div>

      <p class="hint">
        {palette.tokens['series_1']} sits at {palette.against_surface}:1 against
        the page ({preview.thresholds.graphical}:1 needed to see a line),
        headings at {palette.heading_ratio}:1 ({preview.thresholds.text}:1 for
        AA text).
      </p>

      {palette.adjustments.map((adjustment, index) => (
        <div class="adjust-row" key={index}>
          <Swatch hex={adjustment.original} label="you asked for" />
          <span class="arrow">→</span>
          <Swatch hex={adjustment.applied} label="used" />
          <span class="adjust-why">
            <strong>{adjustment.token.replace(/_/g, ' ')}</strong> — {adjustment.reason}
            <br /><span class="hint">{adjustment.detail}</span>
          </span>
        </div>
      ))}

      {logo.path && (logo.ok
        ? <div class="adjust-row"><img class="logo-preview" src={logo.data_uri} alt="" /></div>
        : <p class="warn">{logo.error}</p>)}
    </div>
  );
}
