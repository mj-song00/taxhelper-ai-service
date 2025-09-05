package texhelper.chatservice.domain.law.controller;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.tags.Tag;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;
import texhelper.chatservice.domain.law.service.LawService;


@Tag(name = "법제처 API", description = "법제처 API 호출 및 저장")
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/")
@Slf4j
public class LawController {
    private final LawService lawService;

    @Operation(summary = "법제처 API 요청", description = "데이터를 요청합니다." )
    @GetMapping("")
    public void getData(){
        lawService.saveLawFromApi();
    }
}
