# Only Hensun identity products replace these two vendor inputs. No cache edits.
if(CONFIG_BOARD_TYPE_HENSUN_CAM_PILOT_V1 OR CONFIG_BOARD_TYPE_HENSUN_DESK_V1
   OR CONFIG_BOARD_TYPE_HENSUN_NOCAM_PILOT_V1)
    idf_component_get_property(hensun_wifi_dir 78__esp-wifi-connect COMPONENT_DIR)
    idf_component_get_property(hensun_wifi_lib 78__esp-wifi-connect COMPONENT_LIB)
    idf_build_get_property(hensun_python PYTHON)
    set(hensun_wifi_generator "${CMAKE_CURRENT_LIST_DIR}/../scripts/prepare_hensun_wifi_security.py")
    set(hensun_wifi_output "${CMAKE_BINARY_DIR}/hensun_wifi_security")
    set_property(DIRECTORY APPEND PROPERTY CMAKE_CONFIGURE_DEPENDS
        "${hensun_wifi_generator}" "${hensun_wifi_dir}/idf_component.yml"
        "${hensun_wifi_dir}/wifi_configuration_ap.cc"
        "${hensun_wifi_dir}/assets/wifi_configuration.html")
    execute_process(COMMAND "${hensun_python}" "${hensun_wifi_generator}"
        --component-dir "${hensun_wifi_dir}" --output-dir "${hensun_wifi_output}"
        RESULT_VARIABLE hensun_wifi_result ERROR_VARIABLE hensun_wifi_error)
    if(NOT hensun_wifi_result EQUAL 0)
        message(FATAL_ERROR "Hensun Wi-Fi security overlay failed: ${hensun_wifi_error}")
    endif()

    get_target_property(hensun_wifi_sources "${hensun_wifi_lib}" SOURCES)
    set(hensun_wifi_retained "")
    set(hensun_wifi_replaced 0)
    foreach(hensun_wifi_source IN LISTS hensun_wifi_sources)
        get_filename_component(hensun_wifi_name "${hensun_wifi_source}" NAME)
        if(hensun_wifi_name STREQUAL "wifi_configuration_ap.cc"
           OR hensun_wifi_name STREQUAL "wifi_configuration.html.S")
            math(EXPR hensun_wifi_replaced "${hensun_wifi_replaced} + 1")
        else()
            list(APPEND hensun_wifi_retained "${hensun_wifi_source}")
        endif()
    endforeach()
    if(NOT hensun_wifi_replaced EQUAL 2)
        message(FATAL_ERROR "Hensun Wi-Fi security overlay could not replace both vendor inputs")
    endif()
    set_property(TARGET "${hensun_wifi_lib}" PROPERTY SOURCES "${hensun_wifi_retained}")
    target_sources("${hensun_wifi_lib}" PRIVATE "${hensun_wifi_output}/wifi_configuration_ap.cc")
    target_add_binary_data("${hensun_wifi_lib}"
        "${hensun_wifi_output}/hensun_wifi_configuration.html" TEXT
        RENAME_TO "wifi_configuration.html")
    # The embed command is defined here, while the component target lives in a
    # different directory. An explicit target carries its generation dependency.
    add_custom_target(hensun_wifi_embed
        DEPENDS "${CMAKE_BINARY_DIR}/hensun_wifi_configuration.html.S")
    add_dependencies("${hensun_wifi_lib}" hensun_wifi_embed)
endif()
