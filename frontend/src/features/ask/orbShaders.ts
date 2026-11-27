export const orbVertexShader = `
attribute vec2 a_position;

void main() {
  gl_Position = vec4(a_position, 0.0, 1.0);
}
`

export const orbFragmentShader = `
#ifdef GL_FRAGMENT_PRECISION_HIGH
precision highp float;
#else
precision mediump float;
#endif

uniform float u_size;
uniform float u_phase;
uniform float u_flow;

mat2 rotation(float angle) {
  float c = cos(angle);
  float s = sin(angle);
  return mat2(c, s, -s, c);
}

float lobe(float value, float center, float width) {
  float distance = (value - center) / width;
  return exp(-distance * distance);
}

float current(vec3 p) {
  float curl = sin(3.8 * p.x + 3.1 * p.z + 1.7 * p.y);
  return p.y + 0.18 * curl
    + 0.072 * sin(8.0 * p.x - 1.8 * p.z + curl + 3.0 * p.y)
    + 0.024 * sin(17.0 * p.x + 7.6 * p.z + 12.0 * p.y);
}

void main() {
  vec2 p = (gl_FragCoord.xy / u_size - 0.5) * 2.56;
  float angle = atan(p.y, p.x + 0.00001);
  float phase = u_phase;
  float swell = 0.078 * sin(3.0 * angle + phase * 1.7)
    + 0.042 * sin(5.0 * angle - phase * 1.3)
    + 0.033 * cos(2.0 * angle + phase * 2.2);
  vec2 q = p / (1.0 + u_flow * swell);
  q += u_flow * 0.034 * vec2(sin(q.y * 4.2 + phase * 2.4), sin(q.x * 4.6 - phase * 1.9));

  float radius = length(q);
  float alpha = 1.0 - smoothstep(1.0 - 1.7 / u_size, 1.0 + 1.7 / u_size, radius);
  if (alpha <= 0.0) discard;

  float depth = sqrt(max(0.0, 1.0 - dot(q, q)));
  vec3 normal = normalize(vec3(q, depth));
  vec3 viewRay = vec3(0.0, 0.0, -1.0);
  vec3 ray = refract(viewRay, normal, 0.73);
  vec3 back = normal - 2.0 * dot(normal, ray) * ray;

  // The front and back currents move around the same tilted axis.
  vec3 front = normal;
  float tilt = -0.24 + 0.18 * sin(phase * 0.72);
  mat2 spin = rotation(0.27 + phase * 0.62);
  front.xz = spin * front.xz;
  back.xz = spin * back.xz;
  front.xy = rotation(tilt) * front.xy;
  back.xy = rotation(tilt) * back.xy;
  float layer = current(mix(front, back, 0.22));
  float refractedLayer = current(back);

  vec3 color = mix(vec3(0.33, 0.23, 0.43), vec3(0.77, 0.48, 0.64), smoothstep(-0.95, -0.37, layer));
  color = mix(color, vec3(0.96, 0.66, 0.57), smoothstep(-0.48, -0.07, layer));
  color = mix(color, vec3(0.64, 0.65, 0.85), smoothstep(-0.15, 0.42, layer));
  color = mix(color, vec3(0.95, 0.75, 0.64), smoothstep(0.51, 1.08, layer));

  float pool = pow(max(dot(normal, normalize(vec3(-0.19, 0.43, 0.82))), 0.0), 9.0);
  color = mix(color, vec3(0.74, 0.93, 0.95), pool * 0.90);
  float violet = pow(max(dot(normal, normalize(vec3(0.70, -0.06, 0.64))), 0.0), 14.0);
  color = mix(color, vec3(0.75, 0.62, 0.84), violet * 0.43);

  float ribbon = lobe(layer, -0.30, 0.37);
  float fold = sin(25.0 * layer + 0.6 * sin(11.0 * front.x + 3.0 * front.z));
  color -= vec3(0.12, 0.078, 0.066) * pow(0.5 - 0.5 * fold, 5.0) * ribbon;
  color += vec3(0.22, 0.17, 0.25) * pow(0.5 + 0.5 * fold, 5.0) * ribbon;
  float filament = sin(54.0 * layer + 0.8 * sin(5.0 * front.z - 6.0 * front.y));
  color = mix(color, vec3(1.0, 0.86, 0.76), pow(0.5 + 0.5 * filament, 20.0) * ribbon * 0.71);
  float caustic = pow(0.5 + 0.5 * sin(34.0 * refractedLayer), 12.0);
  color += vec3(0.12, 0.087, 0.20) * caustic * lobe(layer, -0.34, 0.57);

  // Grazing light makes warm and cool arcs around the transparent rim.
  float fresnel = pow(1.0 - depth, 2.2);
  float warm = 0.5 + 0.5 * sin(2.0 * angle + 1.1);
  vec3 rim = mix(vec3(0.81, 0.74, 0.98), vec3(1.0, 0.81, 0.59), warm);
  color = mix(color, rim, fresnel * 0.84);
  color -= vec3(0.14, 0.095, 0.055) * lobe(radius, 0.964, 0.016) * (0.62 - 0.46 * warm);
  color = mix(color, rim, lobe(radius, 0.994, 0.012) * 0.62);

  vec3 reflected = reflect(viewRay, normal);
  float key = max(dot(reflected, normalize(vec3(-0.72, 0.80, 0.22))), 0.0);
  float side = max(dot(reflected, normalize(vec3(0.91, 0.4, -0.13))), 0.0);
  float base = max(dot(reflected, normalize(vec3(0.06, -0.85, -0.16))), 0.0);
  color += vec3(0.16, 0.12, 0.10) * pow(key, 8.0);
  color = mix(color, vec3(1.0, 0.94, 0.84), pow(key, 88.0) * 0.77);
  color = mix(color, vec3(0.99, 0.88, 0.82), pow(side, 58.0) * 0.77);
  color += vec3(0.23, 0.15, 0.085) * pow(base, 19.0);
  color = mix(color, vec3(1.0, 0.94, 0.79), lobe(radius, 0.90, 0.017) * pow(max(0.0, -q.y), 5.0) * 0.68);

  gl_FragColor = vec4(clamp(color, 0.0, 1.0), alpha);
}
`
