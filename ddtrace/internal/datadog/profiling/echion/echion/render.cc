#include <echion/frame.h>
#include <echion/render.h>

// ------------------------------------------------------------------------
void WhereRenderer::render_frame(Frame& frame)
{
    auto name_str = string_table.lookup(frame.name);
    auto filename_str = string_table.lookup(frame.filename);
    auto line = frame.location.line;

    if (filename_str.rfind("native@", 0) == 0)
    {
        WhereRenderer::get().render_message(
            "\033[38;5;248;1m" + name_str + "\033[0m \033[38;5;246m(" + filename_str +
            "\033[0m:\033[38;5;246m" + std::to_string(line) + ")\033[0m");
    }
    else
    {
        WhereRenderer::get().render_message("\033[33;1m" + name_str + "\033[0m (\033[36m" +
                                            filename_str + "\033[0m:\033[32m" +
                                            std::to_string(line) + "\033[0m)");
    }
}

// ------------------------------------------------------------------------
void MojoRenderer::render_frame(Frame& frame)
{
    frame_ref(frame.cache_key);
}
