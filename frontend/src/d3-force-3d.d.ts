declare module 'd3-force-3d' {
  interface Simulation<N> {
    alpha(value: number): this
    force(name: string, value: unknown): this
    stop(): this
    tick(iterations?: number): this
  }

  interface LinkForce<N> {
    id(value: (node: N) => number): this
    distance(value: number): this
    strength(value: number): this
  }

  interface StrengthForce {
    strength(value: number): this
  }

  interface AxisForce<N> {
    strength(value: number): this
  }

  export function forceSimulation<N extends object>(nodes: N[], dimensions?: number): Simulation<N>
  export function forceLink<N extends object>(links: Array<{ source: number; target: number }>): LinkForce<N>
  export function forceManyBody<N extends object>(): StrengthForce
  export function forceCollide<N extends object>(radius: number): unknown
  export function forceCenter(x?: number, y?: number, z?: number): unknown
  export function forceX<N extends object>(value: (node: N) => number): AxisForce<N>
  export function forceY<N extends object>(value: (node: N) => number): AxisForce<N>
  export function forceZ<N extends object>(value: (node: N) => number): AxisForce<N>
}
