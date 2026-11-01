/**
 * Deterministic screen-space labels for orbiting planets.
 *
 * Move a caption only when its current slot is genuinely obstructed.
 * Explore nearby empty directions before hiding it. The returned position is
 * relative to the *moving planet*, so it stays attached during revolution.
 */
export type CaptionBox = {
  left: number
  top: number
  right: number
  bottom: number
}
export type OrbitCaptionItem = {
  id: string
  x: number
  y: number
  radius: number
  width: number
  height: number
  priority: number
}
export type OrbitCaptionSlot = {
  dx: number
  dy: number
}
export type OrbitCaptionPlacement = OrbitCaptionSlot & {
  id: string
  box: CaptionBox
}
export function boxesOverlap(a: CaptionBox, b: CaptionBox, gap = 0): boolean {
  return a.left < b.right + gap && a.right > b.left - gap &&
    a.top < b.bottom + gap && a.bottom > b.top - gap
}
function rect(x: number, y: number, width: number, height: number): CaptionBox {
  return { left: x-width/2, right: x+width/2,
    top: y-height/2, bottom: y+height/2 }
}
function candidateSlots(item: OrbitCaptionItem): OrbitCaptionSlot[] {
  // Keep the nearest CARD EDGE within 7-12px of the actual planet rim.
  // Previous candidates multiplied whole caption dimensions by 1.85:
  // their centers could be 250+px away, visually severing attribution.
  const r=item.radius
  const hw=item.width/2
  const hh=item.height/2
  const gap=9
  const edge=r+gap
  const corner=r*.78+gap
  return [
    {dx:0,dy:-(edge+hh)},
    {dx:0,dy:edge+hh},
    {dx:-(edge+hw),dy:0},
    {dx:edge+hw,dy:0},
    {dx:-(corner+hw),dy:-(corner+hh)},
    {dx:corner+hw,dy:-(corner+hh)},
    {dx:-(corner+hw),dy:corner+hh},
    {dx:corner+hw,dy:corner+hh},
    // Minor tangential shifts, still adjacent to the same star.
    {dx:-hw*.22,dy:-(edge+hh)},
    {dx:hw*.22,dy:-(edge+hh)},
    {dx:-hw*.22,dy:edge+hh},
    {dx:hw*.22,dy:edge+hh},
    {dx:-(edge+hw),dy:-hh*.34},
    {dx:edge+hw,dy:-hh*.34},
    {dx:-(edge+hw),dy:hh*.34},
    {dx:edge+hw,dy:hh*.34},
  ]
}

export function placeOrbitCaptions(
  items: readonly OrbitCaptionItem[],
  bounds: CaptionBox,
  overlays: readonly CaptionBox[],
  previous: Readonly<Record<string, OrbitCaptionSlot>> = {},
): OrbitCaptionPlacement[] {
  const placed: OrbitCaptionPlacement[] = []
  const planets = items.map(item=>({
    id:item.id,
    box:rect(item.x,item.y,item.radius*2+12,item.radius*2+12),
  }))
  const rank=[...items].sort((a,b)=>b.priority-a.priority||a.id.localeCompare(b.id))
  const available=(item:OrbitCaptionItem,slot:OrbitCaptionSlot,gap:number) => {
    const box=rect(item.x+slot.dx,item.y+slot.dy,item.width,item.height)
    const inside=box.left>=bounds.left&&box.right<=bounds.right&&
      box.top>=bounds.top&&box.bottom<=bounds.bottom
    if(!inside||overlays.some(o=>boxesOverlap(box,o,3)) ||
      placed.some(o=>boxesOverlap(box,o.box,gap)) ||
      planets.some(p=>p.id!==item.id&&boxesOverlap(box,p.box,3)))
      return null
    return box
  }
  // Reserve still-clear previous labels BEFORE considering any fresh slot.
  // A tiny change of position cannot cause a higher-ranked caption to
  // steal another caption's long-held location.
  for(const item of rank) {
    const old=previous[item.id]
    if(!old)continue
    // Old positions from a previous build/zoom may be far away. Only keep
    // a slot if it is STILL adjacent to this planet, not simply collision-free.
    const nearSlots=candidateSlots(item)
    const nearest=nearSlots.some(slot =>
      Math.hypot(slot.dx-old.dx,slot.dy-old.dy)<=12)
    const box=nearest?available(item,old,0):null
    if(box)placed.push({id:item.id,dx:old.dx,dy:old.dy,box})
  }
  const retainedIds=new Set(placed.map(p=>p.id))
  for (const item of rank) {
    if(retainedIds.has(item.id))continue
    const old=previous[item.id]
    const valid=(slot:OrbitCaptionSlot)=>available(item,slot,5)
    const candidates=candidateSlots(item)
    let winner: OrbitCaptionPlacement | null=null
    let score=Infinity
    for (let i=0;i<candidates.length;i++) {
      const slot=candidates[i]
      const box=valid(slot)
      if(!box)continue
      // Nearer captions are better, with a small bias toward above the
      // planet. Previous direction is only a tie-breaker when obstructed.
      const travel=Math.hypot(slot.dx,slot.dy)
      const continuity=old?Math.hypot(slot.dx-old.dx,slot.dy-old.dy)*.12:0
      const cost=travel+i*.9+continuity
      if(cost<score) {
        winner={id:item.id,dx:slot.dx,dy:slot.dy,box}
        score=cost
      }
    }
    if(winner)placed.push(winner)
  }
  return placed
}
