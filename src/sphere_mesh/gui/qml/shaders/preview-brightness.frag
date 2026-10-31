#version 440

layout(location = 0) in vec2 qt_TexCoord0;
layout(location = 0) out vec4 fragColor;
layout(std140, binding = 0) uniform buf {
    mat4 qt_Matrix;
    float qt_Opacity;
    float gain;
};
layout(binding = 1) uniform sampler2D source;

void main()
{
    vec4 pixel = texture(source, qt_TexCoord0);
    fragColor = vec4(min(pixel.rgb * gain, vec3(pixel.a)), pixel.a) * qt_Opacity;
}
