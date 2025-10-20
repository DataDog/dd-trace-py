#include <echion/frame.h>
#include <echion/render.h>

// ------------------------------------------------------------------------
void WhereRenderer::render_frame(Frame& frame)
{
    auto maybe_name_str = string_table.lookup(frame.name);
    if (!maybe_name_str) {
        std::cerr << "could not get name for render_frame" << std::endl;
        return;
    }
    const auto& name_str = **maybe_name_str;

    
    auto maybe_filename_str = string_table.lookup(frame.filename);
    if (!maybe_filename_str) {
        std::cerr << "could not get filename for render_frame" << std::endl;
        return;
    }
    const auto& filename_str = **maybe_filename_str;
    
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
