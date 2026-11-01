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
  const r = item.radius + 12
  const hw = item.width/2
  const hh = item.height/2
  // Ordered by label-to-planet distance. These slots cannot drift to a
  // distant, unrelated orbit, and include available space on ALL four sides.
  const base: OrbitCaptionSlot[] = [
    {dx:0,dy:-(r+hh)},
    {dx:r+hw,dy:0},
    {dx:-(r+hw),dy:0},
    {dx:0,dy:r+hh},
    {dx:r+hw*.75,dy:-(r+hh*.55)},
    {dx:-(r+hw*.75),dy:-(r+hh*.55)},
    {dx:r+hw*.75,dy:r+hh*.55},
    {dx:-(r+hw*.75),dy:r+hh*.55},
  ]
  // Small offset variants exploit gaps between planets/previous labels.
  for (const dy of [-(r+hh+12), r+hh+12]) {
    for (const fraction of [-.70, -.38, .38, .70])
      base.push({dx:item.width*fraction,dy})
  }
  for (const dx of [-(r+hw+18),r+hw+18]) {
    for (const fraction of [-1.3,-.65,.65,1.3])
      base.push({dx,dy:item.height*fraction})
  }
  // Last resort: still local to the planet, but permits the visually empty
  // bands common in 16:9 course atlases.
  for (const mul of [1.45,1.85]) {
    base.push(
      {dx:0,dy:-(r+hh)*mul},
      {dx:0,dy:(r+hh)*mul},
      {dx:(r+hw)*mul,dy:0},
      {dx:-(r+hw)*mul,dy:0},
    )
  }
  return base
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
  for (const item of rank) {
    const valid=(slot: OrbitCaptionSlot) => {
      const box=rect(item.x+slot.dx,item.y+slot.dy,item.width,item.height)
      const inside=box.left>=bounds.left&&box.right<=bounds.right&&
        box.top>=bounds.top&&box.bottom<=bounds.bottom
      if(!inside||overlays.some(o=>boxesOverlap(box,o,4)) ||
        placed.some(o=>boxesOverlap(box,o.box,5)) ||
        planets.some(p=>p.id!==item.id&&boxesOverlap(box,p.box,4)))
        return null
      return box
    }
    // The previous slot remains pinned while it is collision-free. A label
    // therefore doesn't jump between two equally good slots every frame.
    const old=previous[item.id]
    const retained=old&&valid(old)
    if(retained) {
      placed.push({id:item.id,dx:old.dx,dy:old.dy,box:retained})
      continue
    }
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
