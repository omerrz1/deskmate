import { useEffect, useRef } from 'react'
import * as THREE from 'three'

/** Small 3D agent orb. Reacts to the pointer and pulses while the agent is working or speaking ('agent-pulse' 0..1). */
export default function Orb() {
  const ref = useRef(null)
  useEffect(() => {
    const el = ref.current
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true })
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    renderer.setSize(el.clientWidth, el.clientHeight)
    el.appendChild(renderer.domElement)
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(45, 1, 0.1, 50)
    camera.position.z = 6.2
    const group = new THREE.Group()
    scene.add(group)
    const wire = (c, o) => new THREE.MeshBasicMaterial({ color: c, wireframe: true, transparent: true, opacity: o })
    const outer = new THREE.Mesh(new THREE.IcosahedronGeometry(2, 2), wire(0x221d1b, 0.2))
    const inner = new THREE.Mesh(new THREE.IcosahedronGeometry(1.2, 1), wire(0xff773b, 0.7))
    const core = new THREE.Mesh(new THREE.SphereGeometry(0.42, 32, 32), new THREE.MeshBasicMaterial({ color: 0xfe5011 }))
    group.add(outer, inner, core)
    const N = 260, pos = new Float32Array(N * 3)
    for (let i = 0; i < N; i++) {
      const r = 2.5 + Math.random() * 1.4, th = Math.random() * 6.283, ph = Math.acos(2 * Math.random() - 1)
      pos.set([r * Math.sin(ph) * Math.cos(th), r * Math.sin(ph) * Math.sin(th), r * Math.cos(ph)], i * 3)
    }
    const pg = new THREE.BufferGeometry()
    pg.setAttribute('position', new THREE.BufferAttribute(pos, 3))
    const dots = new THREE.Points(pg, new THREE.PointsMaterial({ color: 0xfe5011, size: 0.04, transparent: true, opacity: 0.7 }))
    group.add(dots)

    const mouse = { x: 0, y: 0 }
    const onMove = (e) => { mouse.x = (e.clientX / window.innerWidth) * 2 - 1; mouse.y = (e.clientY / window.innerHeight) * 2 - 1 }
    let level = 0, target = 0
    const onPulse = (e) => { target = e.detail || 0 }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('agent-pulse', onPulse)
    let raf, t = 0
    const loop = () => {
      raf = requestAnimationFrame(loop)
      t += reduce ? 0.002 : 0.009
      level += (target - level) * 0.08
      outer.scale.setScalar(1 + level * 0.12 + Math.sin(t * 6) * 0.02 * level)
      core.scale.setScalar(1 + level * 1.1 + Math.sin(t * 2.5) * 0.08)
      outer.rotation.y = t * (0.35 + level); outer.rotation.x = t * 0.15
      inner.rotation.y = -t * (0.6 + level * 1.4); inner.rotation.z = t * 0.2
      dots.rotation.y = t * 0.1
      group.rotation.x += (mouse.y * 0.4 - group.rotation.x) * 0.04
      group.position.x += (mouse.x * 0.4 - group.position.x) * 0.04
      renderer.render(scene, camera)
    }
    loop()
    return () => {
      cancelAnimationFrame(raf)
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('agent-pulse', onPulse)
      scene.traverse((o) => { o.geometry?.dispose?.(); o.material?.dispose?.() })
      renderer.dispose()
      el.removeChild(renderer.domElement)
    }
  }, [])
  return <div ref={ref} className="hero-orb" aria-hidden="true" />
}
