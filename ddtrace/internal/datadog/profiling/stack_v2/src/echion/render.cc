#include <echion/frame.h>
#include <echion/render.h>

// ------------------------------------------------------------------------
void MojoRenderer::render_frame(Frame& frame)
{
    frame_ref(frame.cache_key);
}
