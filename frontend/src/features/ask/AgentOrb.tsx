import { useEffect, useRef } from 'react'
import { orbFragmentShader, orbVertexShader } from './orbShaders'

function compileShader(gl: WebGLRenderingContext, type: number, source: string) {
  const shader = gl.createShader(type)
  if (!shader) return null
  gl.shaderSource(shader, source)
  gl.compileShader(shader)
  if (gl.getShaderParameter(shader, gl.COMPILE_STATUS)) return shader
  gl.deleteShader(shader)
  return null
}

function createRenderer(canvas: HTMLCanvasElement) {
  const gl = canvas.getContext('webgl', { antialias: false, depth: false, premultipliedAlpha: false })
  if (!gl) return null

  const vertex = compileShader(gl, gl.VERTEX_SHADER, orbVertexShader)
  const fragment = compileShader(gl, gl.FRAGMENT_SHADER, orbFragmentShader)
  const program = gl.createProgram()
  const buffer = gl.createBuffer()
  if (vertex && fragment && program) {
    gl.attachShader(program, vertex)
    gl.attachShader(program, fragment)
    gl.linkProgram(program)
  }
  gl.deleteShader(vertex)
  gl.deleteShader(fragment)

  const dispose = () => {
    gl.deleteBuffer(buffer)
    gl.deleteProgram(program)
  }

  if (!program || !buffer || !gl.getProgramParameter(program, gl.LINK_STATUS)) {
    dispose()
    return null
  }

  gl.useProgram(program)
  gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 3, -1, -1, 3]), gl.STATIC_DRAW)
  const position = gl.getAttribLocation(program, 'a_position')
  gl.enableVertexAttribArray(position)
  gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0)
  const sizeUniform = gl.getUniformLocation(program, 'u_size')
  const phaseUniform = gl.getUniformLocation(program, 'u_phase')
  const flowUniform = gl.getUniformLocation(program, 'u_flow')

  return {
    draw(size: number, phase: number, flow: number) {
      if (canvas.width !== size || canvas.height !== size) {
        canvas.width = size
        canvas.height = size
      }
      gl.viewport(0, 0, size, size)
      gl.clear(gl.COLOR_BUFFER_BIT)
      gl.uniform1f(sizeUniform, size)
      gl.uniform1f(phaseUniform, phase)
      gl.uniform1f(flowUniform, flow)
      gl.drawArrays(gl.TRIANGLES, 0, 3)
    },
    dispose,
  }
}

export function AgentOrb({ thinking }: { thinking: boolean }) {
  const orbRef = useRef<HTMLDivElement>(null)
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const fallbackRef = useRef<HTMLDivElement>(null)
  const working = useRef(thinking)
  const wake = useRef<(() => void) | null>(null)

  useEffect(() => {
    working.current = thinking
    wake.current?.()
  }, [thinking])

  useEffect(() => {
    const orb = orbRef.current!
    const canvas = canvasRef.current!
    const fallback = fallbackRef.current!
    const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)')
    let renderer = createRenderer(canvas)
    let frame = 0
    let previousTime = 0
    let phase = 0
    let flow = 0
    let size = 1

    function schedule() {
      if (!frame) frame = requestAnimationFrame(draw)
    }

    function draw(now: number) {
      frame = 0
      const target = working.current && !reducedMotion.matches ? 1 : 0
      const elapsed = previousTime ? Math.min((now - previousTime) / 1000, 0.09) : 0
      // Keep the rotational phase continuous while the current settles.
      if (reducedMotion.matches) {
        phase = 0
        flow = 0
      } else {
        flow += (target - flow) * (1 - Math.exp(-elapsed / (target ? 0.28 : 0.63)))
        if (!target && flow < 0.015) flow = 0
        phase += elapsed * flow
      }

      if (renderer) {
        renderer.draw(size, phase, flow)
        if (!orb.dataset.renderer) orb.dataset.renderer = 'webgl'
      } else {
        if (orb.dataset.renderer) delete orb.dataset.renderer
        const wave = Math.sin(phase * 2.5) * flow
        const width = 50 + 9 * wave
        const height = 50 + 13 * Math.cos(phase * 1.9) * flow
        fallback.style.borderRadius = `${width}% ${100 - width}% ${width}% ${100 - width}% / ${height}% ${height}% ${100 - height}% ${100 - height}%`
        fallback.style.transform = `rotate(${phase * 33}deg) scale(${1 + 0.07 * wave}, ${1 - 0.07 * wave})`
      }

      previousTime = target || flow ? now : 0
      if (previousTime) schedule()
    }

    function resize() {
      size = Math.max(1, Math.min(448, Math.round(canvas.clientWidth * Math.min(window.devicePixelRatio, 1.5))))
      schedule()
    }

    function loseContext(event: Event) {
      event.preventDefault()
      renderer = null
      schedule()
    }

    function restoreContext() {
      renderer = createRenderer(canvas)
      schedule()
    }

    const observer = new ResizeObserver(resize)
    observer.observe(canvas)
    wake.current = schedule
    resize()
    reducedMotion.addEventListener('change', schedule)
    canvas.addEventListener('webglcontextlost', loseContext)
    canvas.addEventListener('webglcontextrestored', restoreContext)
    return () => {
      wake.current = null
      cancelAnimationFrame(frame)
      observer.disconnect()
      reducedMotion.removeEventListener('change', schedule)
      canvas.removeEventListener('webglcontextlost', loseContext)
      canvas.removeEventListener('webglcontextrestored', restoreContext)
      renderer?.dispose()
    }
  }, [])

  return <div className="agent-orb" ref={orbRef} aria-hidden="true" data-thinking={thinking}>
    <div className="orb-fallback" ref={fallbackRef} />
    <canvas ref={canvasRef} />
  </div>
}
