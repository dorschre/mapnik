if(BUILD_DEMO_VIEWER)
    add_subdirectory(viewer)
endif()

if(BUILD_DEMO_CPP)
    add_subdirectory(c++)
    add_subdirectory(ground_truth)
endif()
