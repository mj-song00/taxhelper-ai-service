package texhelper.chatservice.domain.law.dto.request;

import com.fasterxml.jackson.annotation.JsonProperty;
import lombok.Getter;
import texhelper.chatservice.domain.law.entity.Articles;

import java.util.Collections;
import java.util.List;
import java.util.stream.Collectors;


@Getter
public class ArticleRequest {

    @JsonProperty("조문단위")
    private List<ArticleUnitRequest> units;

    public List<Articles> toEntityList() {
        if (units == null) return Collections.emptyList();
        return units.stream()
                .map(ArticleUnitRequest::toEntity)
                .collect(Collectors.toList());
    }
}
