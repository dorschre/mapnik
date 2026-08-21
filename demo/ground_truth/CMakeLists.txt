add_executable(mapnik-ground-truth-demo visual_ground_truth_demo.cpp)
target_link_libraries(mapnik-ground-truth-demo PRIVATE
    mapnik::agg
    mapnik::mapnik
    ICU::data ICU::i18n ICU::uc
)

add_executable(mapnik-ground-truth-render visual_ground_truth_render.cpp)
target_link_libraries(mapnik-ground-truth-render PRIVATE
    mapnik::agg
    mapnik::mapnik
    ICU::data ICU::i18n ICU::uc
)

# Flat C entry point for FFI consumers (see mapnik_ground_truth.py).
add_library(mapnik-ground-truth-c SHARED mapnik_ground_truth_c.cpp)
set_target_properties(mapnik-ground-truth-c PROPERTIES
    CXX_VISIBILITY_PRESET default
    VISIBILITY_INLINES_HIDDEN OFF
    PREFIX "lib"
)
target_link_libraries(mapnik-ground-truth-c PRIVATE
    mapnik::agg
    mapnik::mapnik
    ICU::data ICU::i18n ICU::uc
)
