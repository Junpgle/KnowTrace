/**
 * Screen-space leaf captions deliberately stay smaller than chapter-level
 * navigation cards. These values are CSS-pixel dimensions, independent of
 * zoom, device pixel ratio or sprite texture resolution.
 */
export function leafCaptionDimensions(
  selected: boolean,
  related: boolean,
  focusActive: boolean,
) {
  const width = !focusActive ? 150 : selected ? 164 : related ? 144 : 128
  return {
    width,
    height: focusActive && (selected || related)
      ? width * 180 / 640
      : width * 150 / 520,
  }
}

/** Selection keeps all node positions but MUST initiate a camera transition. */
export function shouldAnimateDetailFocus(
  selectedChanged: boolean,
  layerChanged: boolean,
  preservePositions: boolean,
): boolean {
  return selectedChanged && !layerChanged && preservePositions
}
