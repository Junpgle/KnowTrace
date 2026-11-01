/**
 * Allocate browse groups by REAL knowledge-point count, from inner to outer
 * elliptical belts. The output order and allocation are deterministic across
 * component rebuilds, relation toggles and back navigation.
 *
 * The outer belts have greater circumference and can hold more planets. When
 * counts tie, an ID tie-breaker makes the assignment stable; identical-sized
 * groups may straddle neighboring belts to preserve spacing.
 */
export type SizeRankedGroup = { id: string; source_count: number }

export function sizeOrderedBrowseBands<T extends SizeRankedGroup>(
  groups: readonly T[],
): T[][] {
  const sorted = [...groups].sort((a,b)=>
    (Number.isFinite(a.source_count)?Math.max(0,a.source_count):0) -
    (Number.isFinite(b.source_count)?Math.max(0,b.source_count):0) ||
    a.id.localeCompare(b.id))
  const count = sorted.length
  if (!count) return []
  const rings = count <= 3 ? 1 : count <= 8 ? 2 :
    count <= 18 ? 2 : count <= 30 ? 3 : count <= 48 ? 4 :
      Math.min(8,Math.ceil(count/11))
  const weights = Array.from({length:rings},(_,i)=>1+i*.28)
  const weightTotal=weights.reduce((a,b)=>a+b,0)
  const exact=weights.map(w=>count*w/weightTotal)
  const capacities=exact.map(Math.floor)
  const remaining=count-capacities.reduce((a,b)=>a+b,0)
  const byRemainder=exact.map((value,i)=>({
    i,remainder:value-Math.floor(value),
  })).sort((a,b)=>b.remainder-a.remainder||b.i-a.i)
  for(let j=0;j<remaining;j++)capacities[byRemainder[j].i]++
  const bands:T[][]=[]
  let cursor=0
  for (const capacity of capacities) {
    if (capacity>0)bands.push(sorted.slice(cursor,cursor+capacity))
    cursor+=capacity
  }
  return bands
}
