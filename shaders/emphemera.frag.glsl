#version 300 es
precision highp float;
precision highp int;

uniform vec2 uResolution;
uniform float uTime;
uniform float uProgress;
uniform float uHoldProgress;
uniform int uPhase;
uniform int uFrame;
uniform sampler2D uSdf;
uniform vec2 uHalfSize;
uniform float uRange;
uniform float uDrip;
uniform float uAmp;
uniform float uBlur;
uniform vec3 uFreq;
uniform float uLead;
uniform float uTrail;
uniform float uResidue;
out vec4 fragColor;

#define saturate(i) clamp(i,0.,1.)
#define linearstep(a,b,t) saturate(((t)-(a)) / ((b)-(a)))
#define repeat(i,n) for(int i=0;i<n;i++)

const int SAMPLES = 10;

float ign(vec2 p) {
  vec3 magic = vec3(0.06711056, 0.00583715, 52.9829189);
  return fract(magic.z * fract(dot(p, magic.xy)));
}
float ignAnimated(vec2 p) {
  return fract(ign(p) + float(uFrame) * 0.61803398875);
}

mat3 getOrthogonalBasis(vec3 z) {
  z = normalize(z);
  vec3 up = abs(z.y) < 0.99 ? vec3(0.0, 1.0, 0.0) : vec3(0.0, 0.0, 1.0);
  vec3 x = normalize(cross(up, z));
  return mat3(x, cross(z, x), z);
}

vec3 cyclicNoise(vec3 p, float pump) {
  vec4 sum = vec4(0.0);
  mat3 basis = getOrthogonalBasis(vec3(-1.0, 2.0, -3.0));
  repeat(i, 5)
  {
    p *= basis;
    p += sin(p.yzx);
    sum += vec4(cross(cos(p), sin(p.zxy)), 1.0);
    sum *= pump;
    p *= 2.0;
  }
  return sum.xyz / sum.w;
}

float shape(vec2 p) {
  vec2 uv = vec2(p.x / uHalfSize.x, -p.y / uHalfSize.y) * 0.5 + 0.5;
  float outside = length(max(abs(p) - uHalfSize, 0.0));
  float t = texture(uSdf, clamp(uv, 0.0, 1.0)).r;
  return (0.5 - t) * 2.0 * uRange + outside;
}

vec3 ephemeralColor(float t) {
  return saturate(1.0 - abs(3.0 * t - vec3(1.0, 1.5, 2.0)));
}

float residueTrail(float x01) {
  float front = mix(-uLead, 1.0 + uLead, uProgress);
  if (uPhase == 2) front += uHoldProgress * uTrail * 6.0;
  float dd = x01 - front;
  float behind = 1.0 - smoothstep(0.0, uLead, dd);
  return uResidue * exp(dd / uTrail) * behind;
}

float pulse(float x01) {
  float front = mix(-uLead, 1.0 + uLead, uProgress);
  if (uPhase == 2) front += uHoldProgress * uTrail * 6.0;
  float peak = 1.0 - smoothstep(0.0, uLead, abs(x01 - front));
  float trail = residueTrail(x01);

  return max(peak, trail);
}

void main() {
  vec2 fragCoord = gl_FragCoord.xy;
  vec2 p = fragCoord / uResolution * 2.0 - 1.0;
  p.x *= uResolution.x / uResolution.y;
  float d = 1.0 / uResolution.y;

  float dice = ignAnimated(fragCoord);
  vec3 aa = vec3(0.0);

  float x01 = p.x / uHalfSize.x * 0.5 + 0.5;
  float k = (uPhase == 1 || uPhase == 2 || uPhase == 3)
      ? pulse(x01) : 0.0;
  float sweepEdge = mix(-0.12, 1.12, uProgress);
  float sweep = smoothstep(sweepEdge - 0.025, sweepEdge + 0.025, x01);
  float residueFade = 1.0 - smoothstep(0.72, 1.0, uProgress);
  float dissolveTrail = residueTrail(x01) * residueFade;
  float visibility = 1.0;
  if (uPhase == 0) visibility = 0.0;
  else if (uPhase == 1) visibility = 1.0 - sweep;
  else if (uPhase == 3) visibility = max(sweep, dissolveTrail);

  float flow = 3.0;

  repeat(i, SAMPLES)
  {
    vec2 pt = p;
    float e = (float(i) + dice) / float(SAMPLES);
    float e2 = e * e;

    pt.y -= (1.0 - exp(-uDrip * k * e2)) * abs(pt.y) * sign(pt.y);
    vec2 drift = vec2(uProgress * flow, 0.0);
    pt += uAmp * k * e2 * cyclicNoise(
          uFreq * vec3(p - drift, uTime - e2), 1.5).xy;
    vec3 color = 3.0 / float(SAMPLES) * ephemeralColor(e);

    aa += color * linearstep(d + uBlur * k * e2, -d, shape(pt));
  }

  aa *= visibility;
  fragColor = vec4(aa, max(aa.r, max(aa.g, aa.b)));
}
